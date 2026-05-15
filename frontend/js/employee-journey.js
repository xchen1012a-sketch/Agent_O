(function () {
  'use strict';

  var AgentO = window.AgentO = window.AgentO || {};

  var DEFAULT_DIMENSIONS = [
    { key: 'product_knowledge', label: '产品知识' },
    { key: 'compliance_expression', label: '合规表达' },
    { key: 'needs_discovery', label: '需求挖掘' },
    { key: 'sales_expression', label: '销售沟通' },
    { key: 'objection_handling', label: '异议处理' },
    { key: 'closing_skill', label: '成交收口' },
  ];

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function clampScore(value) {
    var num = Number(value);
    if (!Number.isFinite(num)) return null;
    return Math.max(0, Math.min(100, Math.round(num * 10) / 10));
  }

  function scoreText(value) {
    var score = clampScore(value);
    if (score == null) return '--';
    return String(score.toFixed(1)).replace(/\.0$/, '');
  }

  function text(value) {
    return String(value == null ? '' : value).trim();
  }

  function normalizeDimensions(raw) {
    var source = Array.isArray(raw) && raw.length ? raw : DEFAULT_DIMENSIONS;
    return source.map(function (item, index) {
      item = item || {};
      var fallback = DEFAULT_DIMENSIONS[index] || DEFAULT_DIMENSIONS[0];
      return {
        key: text(item.key || fallback.key),
        label: text(item.label || fallback.label),
      };
    }).filter(function (item) { return item.key; }).slice(0, 8);
  }

  function normalizeAbilityValues(raw, dimensions, score) {
    raw = raw && typeof raw === 'object' ? raw : {};
    var fallback = clampScore(score);
    if (fallback == null) fallback = 0;
    var out = {};
    dimensions.forEach(function (dim) {
      var value = clampScore(raw[dim.key]);
      out[dim.key] = value == null ? fallback : value;
    });
    return out;
  }

  function normalizeNode(raw, index, dimensions) {
    raw = raw || {};
    var score = clampScore(raw.score);
    var details = raw.details && typeof raw.details === 'object' ? raw.details : {};
    var dayIndex = Number(raw.day_index || raw.dayIndex || index + 1) || index + 1;
    var riskLevel = text(raw.risk_level || raw.riskLevel).toLowerCase();
    if (!riskLevel && score != null) {
      riskLevel = score < 60 ? 'high' : (score < 85 ? 'medium' : 'low');
    }
    return {
      dayIndex: dayIndex,
      label: text(raw.label || ('Day ' + dayIndex)),
      title: text(raw.title || ('Day ' + dayIndex + ' 训练记录')),
      subtitle: text(raw.subtitle || ''),
      stageNo: Number(raw.stage_no || raw.stageNo || 0) || 0,
      cycleDayIndex: Number(raw.cycle_day_index || raw.cycleDayIndex || 0) || 0,
      score: score,
      scoreDelta: Number(raw.score_delta || raw.scoreDelta || 0) || 0,
      riskLevel: riskLevel,
      moduleName: text(raw.module_name || raw.moduleName),
      summary: text(raw.summary),
      keyEvent: !!(raw.key_event || raw.keyEvent),
      passed: !!raw.passed,
      abilityValues: normalizeAbilityValues(raw.ability_values || raw.abilityValues, dimensions, score),
      details: {
        tasks: Array.isArray(details.tasks) ? details.tasks : [],
        practice: details.practice || null,
        learning: details.learning || null,
        assessment: details.assessment || null,
      },
    };
  }

  function normalizeEmployeeJourneyPayload(raw) {
    raw = raw && raw.data ? raw.data : (raw || {});
    var dimensions = normalizeDimensions(raw.dimensions);
    var nodes = (Array.isArray(raw.nodes) ? raw.nodes : []).map(function (node, index) {
      return normalizeNode(node, index, dimensions);
    });
    var employee = raw.employee || {};
    var summary = raw.summary || {};
    var firstScore = nodes.length ? nodes[0].score : null;
    var lastScore = nodes.length ? nodes[nodes.length - 1].score : null;
    return {
      employee: {
        id: text(employee.id || employee.employee_id || employee.employeeId),
        name: text(employee.name || employee.employee_name || employee.employeeName || '员工'),
        roleLabel: text(employee.role_label || employee.roleLabel || employee.position || employee.role),
        storeName: text(employee.store_name || employee.storeName || employee.store_id),
        mentorName: text(employee.mentor_name || employee.mentorName),
        initialAbility: text(employee.initial_ability || employee.initialAbility),
      },
      plan: raw.plan || {},
      summary: {
        totalDays: Number(summary.total_days || summary.totalDays || nodes.length || 0) || 0,
        startScore: clampScore(summary.start_score != null ? summary.start_score : firstScore),
        currentScore: clampScore(summary.current_score != null ? summary.current_score : lastScore),
        scoreDelta: Number(summary.score_delta != null ? summary.score_delta : summary.scoreDelta) || 0,
        highRiskCount: Number(summary.high_risk_count || summary.highRiskCount || 0) || 0,
        keyEventCount: Number(summary.key_event_count || summary.keyEventCount || 0) || 0,
        passed: !!summary.passed,
      },
      dimensions: dimensions,
      nodes: nodes,
    };
  }

  function riskLabel(level) {
    if (level === 'high') return '高风险';
    if (level === 'medium') return '中风险';
    if (level === 'low') return '低风险';
    return '未评级';
  }

  function riskClass(level) {
    if (level === 'high') return ' employee-journey-node--risk-high';
    if (level === 'medium') return ' employee-journey-node--risk-medium';
    if (level === 'low') return ' employee-journey-node--risk-low';
    return '';
  }

  function metricCard(label, value, hint) {
    return (
      '<div class="employee-journey-metric">' +
        '<span>' + escapeHtml(label) + '</span>' +
        '<strong>' + escapeHtml(value) + '</strong>' +
        (hint ? '<em>' + escapeHtml(hint) + '</em>' : '') +
      '</div>'
    );
  }

  function renderSummary(data) {
    var summary = data.summary || {};
    var delta = Number(summary.scoreDelta || 0);
    var deltaText = (delta > 0 ? '+' : '') + String(Math.round(delta * 10) / 10).replace(/\.0$/, '');
    return (
      '<div class="employee-journey-summary" aria-label="成长摘要">' +
        metricCard('起点', scoreText(summary.startScore) + ' 分', '入营基线') +
        metricCard('当前', scoreText(summary.currentScore) + ' 分', summary.passed ? '已通过上岗' : '持续训练') +
        metricCard('跃迁', deltaText + ' 分', '14 天变化') +
        metricCard('风险红灯', String(summary.highRiskCount || 0), '自动识别') +
      '</div>'
    );
  }

  function renderAbilityBars(node, dimensions) {
    var html = '<div class="employee-journey-bars" aria-label="六维能力">';
    dimensions.forEach(function (dim) {
      var value = clampScore(node.abilityValues[dim.key]);
      if (value == null) value = 0;
      html +=
        '<div class="employee-journey-bar">' +
          '<div class="employee-journey-bar__head"><span>' + escapeHtml(dim.label) + '</span><strong>' + escapeHtml(scoreText(value)) + '</strong></div>' +
          '<div class="employee-journey-bar__track"><span style="width:' + escapeHtml(String(value)) + '%"></span></div>' +
        '</div>';
    });
    html += '</div>';
    return html;
  }

  function nodeBadge(node) {
    if (node.passed) return '<span class="employee-journey-badge employee-journey-badge--pass">通过上岗</span>';
    if (node.keyEvent && node.riskLevel === 'high') return '<span class="employee-journey-badge employee-journey-badge--risk">风险红灯</span>';
    if (node.keyEvent) return '<span class="employee-journey-badge">关键节点</span>';
    return '<span class="employee-journey-badge employee-journey-badge--muted">' + escapeHtml(riskLabel(node.riskLevel)) + '</span>';
  }

  function renderNode(node, index) {
    var score = node.score == null ? '--' : (scoreText(node.score) + ' 分');
    var classes = 'employee-journey-node' + riskClass(node.riskLevel) + (node.keyEvent ? ' employee-journey-node--key' : '');
    return (
      '<button type="button" class="' + classes + '" data-journey-node-index="' + escapeHtml(String(index)) + '">' +
        '<span class="employee-journey-node__day">Day ' + escapeHtml(String(node.dayIndex)) + '</span>' +
        '<span class="employee-journey-node__rail" aria-hidden="true"><span></span></span>' +
        '<span class="employee-journey-node__body">' +
          '<span class="employee-journey-node__top">' +
            '<strong>' + escapeHtml(node.title) + '</strong>' +
            nodeBadge(node) +
          '</span>' +
          '<span class="employee-journey-node__meta">' +
            '<span>综合 ' + escapeHtml(score) + '</span>' +
            (node.moduleName ? '<span>' + escapeHtml(node.moduleName) + '</span>' : '') +
            '<span>' + escapeHtml(riskLabel(node.riskLevel)) + '</span>' +
          '</span>' +
          (node.subtitle ? '<span class="employee-journey-node__sub">' + escapeHtml(node.subtitle) + '</span>' : '') +
          (node.summary ? '<span class="employee-journey-node__summary">' + escapeHtml(node.summary) + '</span>' : '') +
        '</span>' +
      '</button>'
    );
  }

  function renderFrame(container, data) {
    var employee = data.employee || {};
    var titleName = employee.name || '员工';
    container.className = (container.className || '').replace(/\s*employee-journey-root/g, '') + ' employee-journey-root';

    var html =
      '<div class="employee-journey-shell">' +
        '<header class="employee-journey-hero">' +
          '<div class="employee-journey-hero__main">' +
            '<div class="employee-journey-kicker">成长之旅</div>' +
            '<h1>' + escapeHtml(titleName) + '的成长之旅</h1>' +
            '<p>' + escapeHtml([employee.roleLabel, employee.storeName, employee.mentorName ? ('带教 ' + employee.mentorName) : ''].filter(Boolean).join(' · ')) + '</p>' +
          '</div>' +
          '<div class="employee-journey-hero__status">' +
            '<span>' + escapeHtml(data.summary.passed ? '已通过' : '训练中') + '</span>' +
            '<strong>' + escapeHtml(scoreText(data.summary.currentScore)) + '</strong>' +
          '</div>' +
        '</header>' +
        renderSummary(data);

    if (!data.nodes.length) {
      html += '<div class="employee-journey-empty">暂无成长轨迹</div></div>';
      container.innerHTML = html;
      return;
    }

    html += '<section class="employee-journey-timeline" aria-label="' + escapeHtml(titleName) + '14 天成长时间轴">';
    data.nodes.forEach(function (node, index) {
      html += renderNode(node, index);
    });
    html += '</section></div>';
    container.innerHTML = html;
  }

  function renderTaskList(tasks) {
    if (!tasks || !tasks.length) return '<p class="employee-journey-muted">当日暂无任务明细。</p>';
    var html = '<ul class="employee-journey-detail-list">';
    tasks.slice(0, 8).forEach(function (task) {
      html += '<li><strong>' + escapeHtml(task.title || task.module_name || '训练任务') + '</strong><span>' + escapeHtml(task.ai_feedback || task.description || task.status || '') + '</span></li>';
    });
    html += '</ul>';
    return html;
  }

  function renderDetailPanel(title, body) {
    if (!body) return '';
    return '<section class="employee-journey-detail-panel"><h3>' + escapeHtml(title) + '</h3>' + body + '</section>';
  }

  function openNodeModal(data, nodeIndex) {
    var node = data.nodes[nodeIndex];
    if (!node || !document || !document.createElement) return;
    closeNodeModal();
    var detail = node.details || {};
    var practice = detail.practice || {};
    var learning = detail.learning || {};
    var assessment = detail.assessment || {};
    var modal = document.createElement('div');
    modal.className = 'employee-journey-modal';
    modal.setAttribute('data-employee-journey-modal-root', '1');
    modal.innerHTML =
      '<div class="employee-journey-modal__backdrop" data-journey-modal-close></div>' +
      '<div class="employee-journey-modal__dialog" role="dialog" aria-modal="true" aria-label="' + escapeHtml(node.label + node.title) + '">' +
        '<button type="button" class="employee-journey-modal__close" data-journey-modal-close aria-label="关闭详情">×</button>' +
        '<div class="employee-journey-modal__head">' +
          '<span>' + escapeHtml(node.label) + '</span>' +
          '<h2>' + escapeHtml(node.title) + '</h2>' +
          '<p>' + escapeHtml(node.summary || node.subtitle || '') + '</p>' +
        '</div>' +
        '<div class="employee-journey-modal__score">' +
          '<div><span>综合得分</span><strong>' + escapeHtml(scoreText(node.score)) + '</strong></div>' +
          '<div><span>风险状态</span><strong>' + escapeHtml(riskLabel(node.riskLevel)) + '</strong></div>' +
          '<div><span>训练模块</span><strong>' + escapeHtml(node.moduleName || '--') + '</strong></div>' +
        '</div>' +
        renderDetailPanel('六维能力', renderAbilityBars(node, data.dimensions)) +
        renderDetailPanel('每日任务', renderTaskList(detail.tasks)) +
        renderDetailPanel('陪练记录', practice && (practice.coach_summary || practice.improvement_advice)
          ? '<p>' + escapeHtml(practice.coach_summary || '') + '</p><p>' + escapeHtml(practice.improvement_advice || '') + '</p>'
          : '') +
        renderDetailPanel('学习评估', learning && (learning.learning_summary || learning.manager_feedback)
          ? '<p>' + escapeHtml(learning.learning_summary || '') + '</p><p>' + escapeHtml(learning.manager_feedback || '') + '</p>'
          : '') +
        renderDetailPanel('考试记录', assessment && (assessment.comment || assessment.score != null)
          ? '<p>得分 ' + escapeHtml(scoreText(assessment.score)) + ' 分 · ' + escapeHtml(assessment.is_pass ? '通过' : '未通过') + '</p><p>' + escapeHtml(assessment.comment || '') + '</p>'
          : '') +
      '</div>';
    modal.addEventListener && modal.addEventListener('click', function (event) {
      var target = event.target;
      if (target && target.closest && target.closest('[data-journey-modal-close]')) {
        closeNodeModal();
      }
    });
    document.body.appendChild(modal);
    var closeBtn = modal.querySelector ? modal.querySelector('.employee-journey-modal__close') : null;
    if (closeBtn && closeBtn.focus) closeBtn.focus();
  }

  function closeNodeModal() {
    if (!document || !document.body || !document.querySelector) return;
    var modal = document.querySelector('[data-employee-journey-modal-root]');
    if (modal && modal.parentElement) modal.parentElement.removeChild(modal);
  }

  function bindTimeline(container, data) {
    if (!container || !container.addEventListener || container.__employeeJourneyBound) return;
    container.addEventListener('click', function (event) {
      var target = event.target;
      var button = target && target.closest ? target.closest('[data-journey-node-index]') : null;
      if (!button) return;
      var index = Number(button.getAttribute('data-journey-node-index'));
      if (Number.isFinite(index)) openNodeModal(container.__employeeJourneyData, index);
    });
    container.__employeeJourneyBound = true;
    container.__employeeJourneyData = data;
  }

  function renderLoading(container, employeeName) {
    container.innerHTML =
      '<div class="employee-journey-shell">' +
        '<div class="employee-journey-skeleton" aria-live="polite">' +
          '<strong>' + escapeHtml(employeeName || '员工成长之旅') + '</strong>' +
          '<span>加载成长轨迹中...</span>' +
        '</div>' +
      '</div>';
  }

  function renderError(container, message) {
    container.innerHTML =
      '<div class="employee-journey-shell">' +
        '<div class="employee-journey-empty">' + escapeHtml(message || '成长轨迹加载失败') + '</div>' +
      '</div>';
  }

  function renderWithPayload(container, raw) {
    var data = normalizeEmployeeJourneyPayload(raw);
    container.__employeeJourneyData = data;
    renderFrame(container, data);
    bindTimeline(container, data);
    return data;
  }

  function renderEmployeeJourney(container, props) {
    if (!container) return null;
    if (props && (Array.isArray(props.nodes) || props.data || props.employee)) {
      return renderWithPayload(container, props);
    }
    props = props || {};
    if (typeof props.apiFetch === 'function') {
      var requestKey = String(props.employeeId || props.id || 'self') + ':' + String(Date.now());
      if (container.dataset) container.dataset.employeeJourneyRequest = requestKey;
      renderLoading(container, props.employeeName || props.userName);
      props.apiFetch({ employeeId: props.employeeId || props.id || '' }).then(function (payload) {
        if (container.dataset && container.dataset.employeeJourneyRequest !== requestKey) return;
        renderWithPayload(container, payload || {});
      }).catch(function (err) {
        if (container.dataset && container.dataset.employeeJourneyRequest !== requestKey) return;
        renderError(container, (err && err.message) || '成长轨迹加载失败');
      });
      return { pending: true, nodes: [] };
    }
    return renderWithPayload(container, { nodes: [] });
  }

  if (document && document.addEventListener) {
    document.addEventListener('keydown', function (event) {
      if (event && event.key === 'Escape') closeNodeModal();
    });
  }

  AgentO.normalizeEmployeeJourneyPayload = normalizeEmployeeJourneyPayload;
  AgentO.renderEmployeeJourney = renderEmployeeJourney;
})();
