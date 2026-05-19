(function () {
  'use strict';

  var AgentO = window.AgentO = window.AgentO || {};

  var SOURCE_LABELS = {
    assessment: '试卷错题',
    practice: '陪练弱项',
    assistant: '在岗高风险',
    qa: '知识问答盲点',
  };

  var SEVERITY_LABELS = { high: '严重', medium: '中等', low: '轻微' };

  function asText(value) {
    return String(value == null ? '' : value).trim();
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function tokenFromOptions(options) {
    options = options || {};
    if (typeof options.tokenProvider === 'function') return asText(options.tokenProvider());
    if (options.token) return asText(options.token);
    try {
      return asText(window.localStorage && window.localStorage.getItem('token'));
    } catch (e) {
      return '';
    }
  }

  function baseUrlFromOptions(options) {
    options = options || {};
    if (typeof options.baseUrl === 'function') return asText(options.baseUrl());
    return asText(options.baseUrl);
  }

  function buildUrl(options, path) {
    var base = baseUrlFromOptions(options);
    return (base || '') + path;
  }

  function authHeaders(options) {
    var token = tokenFromOptions(options);
    var headers = { Accept: 'application/json' };
    if (token) headers.Authorization = 'Bearer ' + token;
    return headers;
  }

  function normalizePayload(payload) {
    var data = (payload && payload.data) || payload || {};
    return {
      userId: asText(data.user_id),
      items: Array.isArray(data.items) ? data.items : [],
      summary: data.summary || { total: 0, by_dimension: [], by_module: [], by_source: {}, recurring_top: [] },
    };
  }

  function buildPath(options) {
    var path = '/api/wrong-questions/my-list';
    if (options && options.targetUserId) {
      path = '/api/wrong-questions/by-user/' + encodeURIComponent(options.targetUserId);
    }
    var query = [];
    if (options && options.source) query.push('source=' + encodeURIComponent(options.source));
    if (options && options.dimension) query.push('dimension=' + encodeURIComponent(options.dimension));
    if (query.length) path += '?' + query.join('&');
    return path;
  }

  function fetchReviewNotebook(options) {
    options = options || {};
    var path = buildPath(options);
    if (typeof options.apiFetch === 'function') {
      return Promise.resolve(options.apiFetch(path, { method: 'GET' })).then(function (payload) {
        if (payload && payload.code != null && Number(payload.code) !== 200) {
          throw new Error((payload && payload.message) || '复盘本加载失败');
        }
        return normalizePayload(payload);
      });
    }
    var url = buildUrl(options, path);
    return fetch(url, { method: 'GET', headers: authHeaders(options) })
      .then(function (resp) {
        if (!resp.ok) throw new Error('review_notebook_request_failed_' + resp.status);
        return resp.json();
      })
      .then(normalizePayload);
  }

  function formatAnswer(value) {
    if (value == null || value === '') return '<span class="wq-answer-empty">未作答</span>';
    if (Array.isArray(value)) return escapeHtml(value.join('、'));
    if (typeof value === 'object') {
      try { return escapeHtml(JSON.stringify(value)); } catch (e) { return ''; }
    }
    return escapeHtml(String(value));
  }

  function renderRecurringTop(summary) {
    var top = (summary && Array.isArray(summary.recurring_top)) ? summary.recurring_top : [];
    if (!top.length) return '';
    var html = '<aside class="wq-recurring"><h3>反复出现的弱项</h3><ul>';
    for (var i = 0; i < top.length; i++) {
      var row = top[i] || {};
      html += '<li>'
        + '<button type="button" class="wq-recurring-item" data-wq-dimension="' + escapeHtml(row.dimension) + '">'
        + '<span class="wq-recurring-rank">#' + (i + 1) + '</span>'
        + '<span class="wq-recurring-label">' + escapeHtml(row.label || row.dimension) + '</span>'
        + '<span class="wq-recurring-count">' + (row.count || 0) + ' 条</span>'
        + (row.severity_high ? '<span class="wq-recurring-high">高 ' + row.severity_high + '</span>' : '')
        + '</button></li>';
    }
    html += '</ul></aside>';
    return html;
  }

  function renderDimensionChips(summary, activeDimension) {
    var rows = (summary && Array.isArray(summary.by_dimension)) ? summary.by_dimension : [];
    var html = '<div class="wq-dim-chips" role="tablist">';
    html += '<button type="button" class="wq-dim-chip' + (activeDimension ? '' : ' is-active') + '" data-wq-dimension="">全部 ' + (summary && summary.total || 0) + '</button>';
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i] || {};
      var dim = asText(row.dimension);
      html += '<button type="button" class="wq-dim-chip wq-dim-' + escapeHtml(dim || 'other')
        + (activeDimension === dim ? ' is-active' : '')
        + '" data-wq-dimension="' + escapeHtml(dim) + '">'
        + escapeHtml(row.label || dim) + ' ' + (row.count || 0)
        + (row.severity_high ? '<span class="wq-dim-high">·高 ' + row.severity_high + '</span>' : '')
        + '</button>';
    }
    html += '</div>';
    return html;
  }

  function renderSourceTabs(summary, activeSource) {
    var by = (summary && summary.by_source) || {};
    var total = (by.assessment || 0) + (by.practice || 0) + (by.assistant || 0) + (by.qa || 0);
    var tabs = [
      { key: '', label: '全部', count: total },
      { key: 'assessment', label: SOURCE_LABELS.assessment, count: by.assessment || 0 },
      { key: 'practice', label: SOURCE_LABELS.practice, count: by.practice || 0 },
      { key: 'assistant', label: SOURCE_LABELS.assistant, count: by.assistant || 0 },
      { key: 'qa', label: SOURCE_LABELS.qa, count: by.qa || 0 },
    ];
    var html = '<div class="wq-source-tabs" role="tablist">';
    for (var i = 0; i < tabs.length; i++) {
      var t = tabs[i];
      var active = activeSource === t.key || (!activeSource && !t.key);
      html += '<button type="button" class="wq-source-tab' + (active ? ' is-active' : '')
        + '" data-wq-source="' + escapeHtml(t.key) + '">'
        + escapeHtml(t.label) + ' <span class="wq-source-tab-count">' + t.count + '</span>'
        + '</button>';
    }
    html += '</div>';
    return html;
  }

  function renderAssessmentBody(item) {
    return '<dl class="wq-card-answers">'
      + '<div><dt>你的答案</dt><dd class="wq-answer-wrong">' + formatAnswer(item.user_answer) + '</dd></div>'
      + '<div><dt>正确答案</dt><dd class="wq-answer-right">' + formatAnswer(item.correct_answer) + '</dd></div>'
    + '</dl>';
  }

  function renderEvidenceBody(item) {
    if (!item.evidence) return '';
    return '<p class="wq-card-evidence">' + escapeHtml(item.evidence) + '</p>';
  }

  function renderItem(item) {
    item = item || {};
    var sourceLabel = SOURCE_LABELS[item.source] || item.source || '';
    var sevLabel = SEVERITY_LABELS[item.severity] || '';
    var body = item.source === 'assessment' ? renderAssessmentBody(item) : renderEvidenceBody(item);
    var actionBtn = '';
    var action = item.suggested_action || {};
    if (action.route) {
      actionBtn = '<button type="button" class="wq-card-action" data-wq-action="'
        + escapeHtml(action.route) + '" data-wq-module="' + escapeHtml(action.module_code || '') + '">'
        + escapeHtml(action.label || '去练') + ' →</button>';
    }
    var masterBtn = '<button type="button" class="wq-card-mastered-btn" data-wq-master-source="'
      + escapeHtml(item.source) + '" data-wq-master-record-id="' + escapeHtml(item.record_id)
      + '" data-wq-master-question-id="' + escapeHtml(item.question_id || '')
      + '" data-wq-master-dimension="' + escapeHtml(item.dimension || '')
      + '" data-wq-master-knowledge-tag="' + escapeHtml(item.knowledge_tag || '')
      + '" data-wq-master-title="' + escapeHtml(item.title || '') + '">标记已掌握</button>';
    return '<article class="wq-card wq-card-source-' + escapeHtml(item.source)
      + ' wq-dim-' + escapeHtml(item.dimension || 'other') + '">'
      + '<header class="wq-card-head">'
        + '<span class="wq-card-source">' + escapeHtml(sourceLabel) + '</span>'
        + '<span class="wq-card-dim">' + escapeHtml(item.dimension_label || '') + '</span>'
        + (sevLabel ? '<span class="wq-card-sev wq-sev-' + escapeHtml(item.severity) + '">' + sevLabel + '</span>' : '')
        + (item.module_label ? '<span class="wq-card-module">' + escapeHtml(item.module_label) + '</span>' : '')
      + '</header>'
      + '<h3 class="wq-card-title">' + escapeHtml(item.title || '') + '</h3>'
      + body
      + '<footer class="wq-card-foot">'
        + '<span>' + escapeHtml(item.occurred_at || '') + '</span>'
        + '<span class="wq-card-foot-actions">' + masterBtn + actionBtn + '</span>'
      + '</footer>'
    + '</article>';
  }

  function renderList(container, items) {
    if (!items.length) {
      container.innerHTML = '<div class="wq-empty">当前筛选下没有错题，继续保持！</div>';
      return;
    }
    var html = '';
    for (var i = 0; i < items.length; i++) html += renderItem(items[i]);
    container.innerHTML = html;
  }

  function callMarkMastered(options, payload) {
    var path = '/api/wrong-questions/mark-mastered';
    var bodyStr = JSON.stringify(payload);
    if (typeof options.apiFetch === 'function') {
      return Promise.resolve().then(function () {
        return options.apiFetch(path, {
          method: 'POST',
          body: bodyStr,
          headers: { 'Content-Type': 'application/json' }
        });
      }).then(function (resp) {
        if (resp && resp.code != null && Number(resp.code) !== 200) {
          throw new Error((resp && resp.message) || '标记失败');
        }
        return resp;
      });
    }
    var url = buildUrl(options, path);
    return fetch(url, {
      method: 'POST',
      headers: Object.assign({}, authHeaders(options), { 'Content-Type': 'application/json' }),
      body: bodyStr,
    }).then(function (resp) {
      if (!resp.ok) throw new Error('mark_mastered_failed_' + resp.status);
      return resp.json();
    }).then(function (resp) {
      if (resp && resp.code != null && Number(resp.code) !== 200) {
        throw new Error((resp && resp.message) || '标记失败');
      }
      return resp;
    });
  }

  function showToast(message) {
    var toast = document.createElement('div');
    toast.className = 'wq-toast';
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(function () { toast.classList.add('wq-toast-visible'); }, 10);
    setTimeout(function () {
      toast.classList.remove('wq-toast-visible');
      setTimeout(function () { toast.remove(); }, 300);
    }, 2000);
  }

  function showRemarkDialog(callback) {
    var overlay = document.createElement('div');
    overlay.className = 'wq-dialog-overlay';

    var dialog = document.createElement('div');
    dialog.className = 'wq-dialog';
    dialog.innerHTML = '<div class="wq-dialog-header">'
      + '<h3>标记为已掌握</h3>'
      + '<button type="button" class="wq-dialog-close">&times;</button>'
      + '</div>'
      + '<div class="wq-dialog-body">'
      + '<label class="wq-dialog-label">请填写说明（留痕）：</label>'
      + '<textarea class="wq-dialog-input" placeholder="例如：已通过考核补考、店长指导后已纠正…" rows="3"></textarea>'
      + '<div class="wq-dialog-hint">至少 2 个字，便于后续追溯</div>'
      + '</div>'
      + '<div class="wq-dialog-footer">'
      + '<button type="button" class="wq-dialog-cancel">取消</button>'
      + '<button type="button" class="wq-dialog-confirm">确认标记</button>'
      + '</div>';

    overlay.appendChild(dialog);
    document.body.appendChild(overlay);

    var input = dialog.querySelector('.wq-dialog-input');
    var closeBtn = dialog.querySelector('.wq-dialog-close');
    var cancelBtn = dialog.querySelector('.wq-dialog-cancel');
    var confirmBtn = dialog.querySelector('.wq-dialog-confirm');

    function close() {
      overlay.classList.add('wq-dialog-closing');
      setTimeout(function () { overlay.remove(); }, 200);
    }

    function confirm() {
      var value = (input.value || '').trim();
      if (value.length < 2) {
        input.classList.add('wq-dialog-input-error');
        return;
      }
      close();
      callback(value);
    }

    closeBtn.addEventListener('click', close);
    cancelBtn.addEventListener('click', close);
    confirmBtn.addEventListener('click', confirm);
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) close();
    });

    input.addEventListener('input', function () {
      input.classList.remove('wq-dialog-input-error');
    });

    setTimeout(function () { input.focus(); }, 100);
  }

  function attachHandlers(root, state, options) {
    function applyState(next) {
      state.source = next.source != null ? next.source : state.source;
      state.dimension = next.dimension != null ? next.dimension : state.dimension;
      renderWith(root, state, options);
    }

    var sourceBar = root.querySelector('[data-wq-source-bar]');
    if (sourceBar) {
      sourceBar.addEventListener('click', function (event) {
        var target = event.target.closest && event.target.closest('[data-wq-source]');
        if (!target) return;
        applyState({ source: target.getAttribute('data-wq-source') || '' });
      });
    }
    var dimBar = root.querySelector('[data-wq-dim-bar]');
    if (dimBar) {
      dimBar.addEventListener('click', function (event) {
        var target = event.target.closest && event.target.closest('[data-wq-dimension]');
        if (!target) return;
        applyState({ dimension: target.getAttribute('data-wq-dimension') || '' });
      });
    }
    var recurringBar = root.querySelector('[data-wq-recurring]');
    if (recurringBar) {
      recurringBar.addEventListener('click', function (event) {
        var target = event.target.closest && event.target.closest('[data-wq-dimension]');
        if (!target) return;
        applyState({ dimension: target.getAttribute('data-wq-dimension') || '' });
      });
    }

    var list = root.querySelector('[data-wq-list]');
    if (list) {
      list.addEventListener('click', function (event) {
        var btn = event.target.closest && event.target.closest('[data-wq-action]');
        if (!btn) return;
        var route = btn.getAttribute('data-wq-action');
        if (typeof options.onAction === 'function') {
          options.onAction({ route: route, moduleCode: btn.getAttribute('data-wq-module') || '' });
        } else if (typeof window.navigateTo === 'function' && route) {
          window.navigateTo(route);
        }
      });

      list.addEventListener('click', function (event) {
        var btn = event.target.closest && event.target.closest('[data-wq-master-source]');
        if (!btn) return;
        showRemarkDialog(function (remark) {
          var card = btn.closest('.wq-card');
          var payload = {
            source: btn.getAttribute('data-wq-master-source') || '',
            source_record_id: Number(btn.getAttribute('data-wq-master-record-id')) || 0,
            question_id: btn.getAttribute('data-wq-master-question-id') || '',
            dimension: btn.getAttribute('data-wq-master-dimension') || '',
            knowledge_tag: btn.getAttribute('data-wq-master-knowledge-tag') || '',
            title: btn.getAttribute('data-wq-master-title') || '',
            remark: remark,
          };
          callMarkMastered(options, payload).then(function () {
            if (card) {
              card.classList.add('wq-card-fadeout');
              setTimeout(function () { card.remove(); }, 300);
            }
            showToast('已标记为已掌握');
          }).catch(function (err) {
            showToast('标记失败：' + (err && err.message || '未知错误'));
          });
        });
      });
    }
  }

  function renderWith(root, state, options) {
    root.innerHTML = '<div class="wq-loading">复盘本加载中…</div>';
    fetchReviewNotebook({
      apiFetch: options.apiFetch,
      baseUrl: options.baseUrl,
      tokenProvider: options.tokenProvider,
      token: options.token,
      targetUserId: options.targetUserId,
      source: state.source,
      dimension: state.dimension,
    }).then(function (result) {
      var masteredCount = (result.summary && result.summary.mastered_count) || 0;
      var masteredText = masteredCount > 0 ? ' <span class="wq-mastered-count">(' + masteredCount + ' 条已掌握)</span>' : '';
      root.innerHTML =
        '<section class="wq-panel" aria-label="复盘本">'
          + '<header class="wq-head">'
            + '<h2>复盘本</h2>'
            + '<span class="wq-total">共 ' + (result.summary.total || 0) + ' 条待复盘' + masteredText + '</span>'
          + '</header>'
          + '<div class="wq-sticky-head">'
            + '<div class="wq-summary-row">'
              + '<div class="wq-summary-main" data-wq-dim-bar>' + renderDimensionChips(result.summary, state.dimension) + '</div>'
              + '<div class="wq-summary-side" data-wq-recurring>' + renderRecurringTop(result.summary) + '</div>'
            + '</div>'
            + '<div data-wq-source-bar>' + renderSourceTabs(result.summary, state.source) + '</div>'
          + '</div>'
          + '<div class="wq-list" data-wq-list></div>'
        + '</section>';
      renderList(root.querySelector('[data-wq-list]'), result.items);
      attachHandlers(root, state, options);
    }).catch(function (err) {
      root.innerHTML = '<div class="wq-error">复盘本加载失败：' + escapeHtml(err && err.message) + '</div>';
      try { console.warn('[review_notebook] load failed', err); } catch (e) {}
    });
  }

  function renderReviewNotebook(container, options) {
    if (!container) return Promise.reject(new Error('container_required'));
    options = options || {};
    var state = {
      source: asText(options.initialSource || ''),
      dimension: asText(options.initialDimension || ''),
    };
    renderWith(container, state, options);
    return Promise.resolve(state);
  }

  AgentO.wrongQuestions = {
    fetch: fetchReviewNotebook,
    render: renderReviewNotebook,
  };
  AgentO.reviewNotebook = AgentO.wrongQuestions;
})();
