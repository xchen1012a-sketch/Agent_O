(function () {
  'use strict';

  var AgentO = window.AgentO = window.AgentO || {};
  var closeTimer = null;

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function normalizeEvent(raw) {
    if (!raw || typeof raw !== 'object') return null;
    var stage = Number(raw.stage || raw.stage_no || raw.unlocked_stage || 0);
    var name = String(raw.name || raw.stage_name || raw.unlocked_stage_name || '').trim();
    if (!stage && !name) return null;
    var score = raw.review_score == null || raw.review_score === '' ? '' : Number(raw.review_score);
    return {
      type: String(raw.type || 'stage_unlocked'),
      stage: stage,
      name: name || ('阶段 ' + stage),
      passedStageName: String(raw.passed_stage_name || '').trim(),
      reviewScore: Number.isFinite(score) ? score.toFixed(1) : '',
    };
  }

  function removeExistingOverlay() {
    var old = document.querySelector('.stage-unlock-overlay');
    if (old && old.parentNode) old.remove();
  }

  function renderStageUnlock(container, props) {
    var event = normalizeEvent(props);
    if (!event || !document || !document.createElement) return null;

    var host = container && typeof container.appendChild === 'function' ? container : document.body;
    if (!host) return null;

    removeExistingOverlay();
    if (closeTimer) {
      clearTimeout(closeTimer);
      closeTimer = null;
    }

    var isCompleted = event.type === 'onboarding_completed';
    var overlay = document.createElement('div');
    overlay.className = 'stage-unlock-overlay';
    overlay.setAttribute('role', 'alert');
    overlay.setAttribute('aria-live', 'assertive');
    overlay.innerHTML =
      '<div class="stage-unlock-card">' +
        '<div class="stage-unlock-aura" aria-hidden="true"></div>' +
        '<div class="stage-unlock-badge" aria-hidden="true">' +
          '<span></span>' +
        '</div>' +
        '<div class="stage-unlock-copy">' +
          '<p class="stage-unlock-kicker">' + escapeHtml(isCompleted ? '上岗资格解锁' : '阶段解锁') + '</p>' +
          '<h2>' + escapeHtml(isCompleted ? '独立上岗' : ('阶段 ' + event.stage + ' · ' + event.name)) + '</h2>' +
          '<p>' + escapeHtml(event.passedStageName ? event.passedStageName + ' 已通过' : '阶段评估已通过') + '</p>' +
        '</div>' +
        '<div class="stage-unlock-score">' +
          '<span>评估得分</span>' +
          '<strong>' + escapeHtml(event.reviewScore || '--') + '</strong>' +
        '</div>' +
      '</div>';

    host.appendChild(overlay);
    setTimeout(function () {
      overlay.className += ' stage-unlock-overlay--visible';
    }, 20);
    closeTimer = setTimeout(function () {
      overlay.className = overlay.className.replace(' stage-unlock-overlay--visible', '') + ' stage-unlock-overlay--closing';
      setTimeout(function () {
        if (overlay.parentNode) overlay.remove();
      }, 260);
    }, 3600);
    return overlay;
  }

  document.addEventListener('agento:stage-unlock', function (event) {
    renderStageUnlock(document.body, event && event.detail);
  });

  AgentO.renderStageUnlock = renderStageUnlock;
})();
