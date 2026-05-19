/**
 * knowledge-feedback.js — AI 教练建议卡片 (B2)
 *
 * 店长首页展示高频问题聚类，支持一键派发学习任务到导购。
 * 依赖：app.js 提供的 apiFetch / showToast / escapeHtml / sanitizeUiText / getCurrentRole / isManagementRole。
 */
(function () {
  'use strict';

  var _kfClustersCache = null;
  var _kfStoreId = '';

  window.loadAndRenderKnowledgeFeedback = async function (storeId) {
    var slot = document.getElementById('kf-card-slot');
    if (!slot) return;
    _kfStoreId = storeId || '';
    slot.innerHTML =
      '<div class="home-bi-proof-card">' +
        '<div class="home-bi-proof-card__body">' +
          '<div class="home-bi-empty kf-loading">高频问题聚类分析中…</div>' +
        '</div>' +
      '</div>';

    try {
      var qs = '?top_n=5';
      if (_kfStoreId) qs += '&store_id=' + encodeURIComponent(_kfStoreId);
      var res = await apiFetch('/api/knowledge-feedback/clusters' + qs, { method: 'GET' });
      _kfClustersCache = (res && res.data && res.data.clusters) || [];
    } catch (e) {
      _kfClustersCache = null;
      slot.innerHTML =
        '<div class="home-bi-proof-card">' +
          '<div class="home-bi-proof-card__body">' +
            '<div class="home-bi-empty">聚类数据加载失败</div>' +
          '</div>' +
        '</div>';
      return;
    }

    renderKnowledgeFeedbackCard(slot);
  };

  function renderKnowledgeFeedbackCard(container) {
    var clusters = _kfClustersCache;
    if (!clusters || !clusters.length) {
      container.innerHTML =
        '<article class="home-bi-proof-card">' +
          '<div class="home-bi-proof-card__head">' +
            '<div>' +
              '<div class="home-bi-proof-card__eyebrow">AI 教练建议</div>' +
              '<h3 class="home-bi-proof-card__title">高频问题聚类</h3>' +
            '</div>' +
          '</div>' +
          '<div class="home-bi-proof-card__body">' +
            '<div class="home-bi-empty">近期暂无足够的高频问题样本（需 ≥2 条相似问题）</div>' +
          '</div>' +
        '</article>';
      return;
    }

    var itemsHtml = clusters.map(function (c) {
      var riskLabel = '';
      var riskClass = '';
      if (c.risk_level === 'high') { riskLabel = '高风险'; riskClass = ' kf-risk-high'; }
      else if (c.risk_level === 'medium') { riskLabel = '中风险'; riskClass = ' kf-risk-medium'; }
      else if (c.risk_level === 'low') { riskLabel = '低风险'; riskClass = ' kf-risk-low'; }

      var tagHtml = c.primary_tag ? '<span class="kf-meta-tag">' + escapeHtml(c.primary_tag) + '</span>' : '';

      return (
        '<div class="kf-cluster-item">' +
          '<div class="kf-cluster-item__main">' +
            '<span class="kf-cluster-rank">#' + c.rank + '</span>' +
            '<div class="kf-cluster-item__body">' +
              '<p class="kf-cluster-question">' + escapeHtml(c.representative_question) + '</p>' +
              '<div class="kf-cluster-meta">' +
                '<span class="kf-meta-count">' + c.count + ' 次</span>' +
                tagHtml +
                (riskLabel ? '<span class="kf-meta-risk' + riskClass + '">' + riskLabel + '</span>' : '') +
              '</div>' +
            '</div>' +
          '</div>' +
          '<button type="button" class="kf-dispatch-btn" data-kf-idx="' + (c.rank - 1) + '">派发学习任务</button>' +
        '</div>'
      );
    }).join('');

    container.innerHTML =
      '<article class="home-bi-proof-card kf-main-card">' +
        '<div class="home-bi-proof-card__head">' +
          '<div>' +
            '<div class="home-bi-proof-card__eyebrow">AI 教练建议</div>' +
            '<h3 class="home-bi-proof-card__title">高频问题聚类 · Top ' + clusters.length + '</h3>' +
          '</div>' +
          '<button type="button" class="kf-refresh-btn" title="刷新聚类">' +
            '<svg class="kf-refresh-icon" viewBox="0 0 16 16" fill="none"><path d="M2 8a6 6 0 0111.33-2.67M14 8a6 6 0 01-11.33 2.67" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><path d="M13.33 2.67V5.33h-2.66M2.67 13.33v-2.66h2.66" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
          '</button>' +
        '</div>' +
        '<div class="home-bi-proof-card__body">' +
          '<div class="kf-cluster-list">' + itemsHtml + '</div>' +
        '</div>' +
      '</article>';

    container.querySelector('.kf-refresh-btn').addEventListener('click', function () {
      loadAndRenderKnowledgeFeedback(_kfStoreId);
    });

    container.querySelectorAll('.kf-dispatch-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var idx = parseInt(btn.getAttribute('data-kf-idx'), 10);
        if (idx >= 0 && idx < clusters.length) {
          openKnowledgeDispatchDialog(clusters[idx]);
        }
      });
    });
  }

  async function openKnowledgeDispatchDialog(cluster) {
    var targetUsers = [];
    try {
      var usersRes = await apiFetch('/api/users', { method: 'GET' });
      var allUsers = (usersRes && usersRes.data && usersRes.data.items) || (usersRes && usersRes.data) || [];
      if (!Array.isArray(allUsers)) allUsers = [];

      var storeFilter = _kfStoreId || '';
      targetUsers = allUsers.filter(function (u) {
        var role = (u.role || '').toLowerCase();
        var isTrainee = role === 'trainee' || role === 'newbie';
        if (!isTrainee) return false;
        if (storeFilter) {
          return (u.store_id || '') === storeFilter;
        }
        return true;
      });
    } catch (e) {
      showToast('无法加载店员列表', 'error');
      return;
    }

    if (!targetUsers.length) {
      showToast('当前门店没有可派发的导购', 'warning');
      return;
    }

    var overlay = document.createElement('div');
    overlay.className = 'ios-sheet-overlay';
    overlay.setAttribute('data-align', 'center');
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) overlay.remove();
    });

    var userRows = targetUsers.map(function (u) {
      var name = sanitizeUiText(u.display_name || u.username || u.user_id || '');
      return (
        '<label class="kf-user-row">' +
          '<input type="checkbox" value="' + escapeHtml(u.user_id || u.username || '') + '" class="kf-user-check" />' +
          '<span class="kf-user-name">' + escapeHtml(name) + '</span>' +
          '<span class="kf-user-role">导购</span>' +
        '</label>'
      );
    }).join('');

    overlay.innerHTML =
      '<div class="ios-sheet-card kf-dispatch-dialog" onclick="event.stopPropagation()">' +
        '<div class="flex items-start justify-between gap-4 px-6 pt-6 pb-2">' +
          '<div>' +
            '<h3 class="text-lg font-semibold text-gray-900">派发学习任务</h3>' +
            '<p class="mt-1 text-sm text-gray-500">该高频问题将推送至导购首页待办</p>' +
          '</div>' +
          '<button type="button" class="kf-dialog-close" aria-label="关闭">' +
            '<svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>' +
          '</button>' +
        '</div>' +
        '<div class="px-6 pb-3">' +
          '<div class="kf-cluster-preview">' +
            '<p class="kf-cluster-preview-q">' + escapeHtml(cluster.representative_question || '') + '</p>' +
            '<p class="kf-cluster-preview-meta">' +
              escapeHtml(cluster.count + ' 次 · ' + (cluster.primary_tag || '未分类') + (cluster.risk_level ? ' · ' + (cluster.risk_level === 'high' ? '高风险' : cluster.risk_level === 'medium' ? '中风险' : '低风险') : '')) +
            '</p>' +
          '</div>' +
        '</div>' +
        '<div class="px-6 pb-2">' +
          '<p class="text-xs text-gray-400 font-medium mb-2">选择派发对象</p>' +
          '<div class="kf-user-list">' + userRows + '</div>' +
          '<p class="text-xs text-gray-400 mt-2">可多选，派发后导购将在「我的任务」中看到该问题。</p>' +
        '</div>' +
        '<div class="flex justify-end gap-3 px-6 pb-6 pt-4 border-t border-gray-100">' +
          '<button type="button" class="kf-dispatch-cancel px-5 py-2.5 rounded-lg text-sm font-medium bg-white border border-gray-300 text-gray-700 hover:bg-gray-50">取消</button>' +
          '<button type="button" class="kf-dispatch-confirm px-5 py-2.5 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">确认派发</button>' +
        '</div>' +
      '</div>';

    document.body.appendChild(overlay);

    var confirmBtn = overlay.querySelector('.kf-dispatch-confirm');
    var cancelBtn = overlay.querySelector('.kf-dispatch-cancel');
    var closeBtn = overlay.querySelector('.kf-dialog-close');

    function remove() { overlay.remove(); }
    cancelBtn.addEventListener('click', remove);
    closeBtn.addEventListener('click', remove);

    confirmBtn.addEventListener('click', async function () {
      var checked = overlay.querySelectorAll('.kf-user-check:checked');
      var targetUserIds = [];
      checked.forEach(function (cb) { targetUserIds.push(cb.value); });

      if (!targetUserIds.length) {
        showToast('请至少选择一位导购', 'warning');
        return;
      }

      confirmBtn.disabled = true;
      confirmBtn.textContent = '派发中...';

      try {
        var res = await apiFetch('/api/knowledge-feedback/dispatch', {
          method: 'POST',
          body: JSON.stringify({
            cluster_signature: cluster.cluster_id || cluster.representative_question,
            representative_question: cluster.representative_question,
            primary_tag: cluster.primary_tag || '',
            top_keywords: cluster.top_keywords || [],
            cluster_count: cluster.count || 0,
            target_user_ids: targetUserIds,
            note: 'AI 教练自动识别的高频问题，请复盘学习。',
          }),
        });
        remove();
        var count = (res && res.data && res.data.dispatched_count) || targetUserIds.length;
        showToast('已派发 ' + count + ' 位导购', 'success');
      } catch (e) {
        confirmBtn.disabled = false;
        confirmBtn.textContent = '确认派发';
        showToast('派发失败：' + ((e && e.message) || '请稍后重试'), 'error');
      }
    });
  }
})();
