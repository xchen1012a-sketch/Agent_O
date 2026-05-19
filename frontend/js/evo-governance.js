(function () {
  'use strict';

  var root = typeof window !== 'undefined' ? window : globalThis;
  var namespace = root.AgentOEvoGovernance || {};

  var PREFLIGHT_LABELS = {
    not_run: '未运行',
    passed: '通过',
    blocked: '阻断',
    missing_cases: '缺用例',
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function renderPreflightBadge(preflight) {
    var data = preflight && typeof preflight === 'object' ? preflight : {};
    var raw = String(data.status || 'not_run').trim();
    var status = Object.prototype.hasOwnProperty.call(PREFLIGHT_LABELS, raw) ? raw : 'not_run';
    var runCount = Number(data.run_count || 0);
    var failedCount = Number(data.failed_count || 0);
    var label = data.label || PREFLIGHT_LABELS[status];
    var meta = runCount > 0 ? ' · ' + runCount + '次' : '';
    var title = '升级前置安全网：' + label;
    if (failedCount > 0) title += '，失败 ' + failedCount + ' 次';
    return '<span class="evo-gov-preflight-badge evo-gov-preflight-badge--' + escapeHtml(status) + '" title="' + escapeHtml(title) + '">' + escapeHtml(label + meta) + '</span>';
  }

  namespace.PREFLIGHT_LABELS = PREFLIGHT_LABELS;
  namespace.renderPreflightBadge = renderPreflightBadge;
  root.AgentOEvoGovernance = namespace;
})();
