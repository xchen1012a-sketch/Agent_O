(function () {
  'use strict';

  var AgentO = window.AgentO = window.AgentO || {};

  var ROOT_ID = 'agent-activity-root';
  var MAX_EVENTS = 4;
  var RECONNECT_DELAY_MS = 3200;
  var ACTIVITY_TABS = ['latest', 'flow', 'detail'];
  var DRAG_STORAGE_KEY = 'agent_activity_position_v1';
  var DRAG_EDGE_PADDING = 12;

  function asText(value) {
    return String(value == null ? '' : value).trim();
  }

  function asNumber(value, fallback) {
    var num = Number(value);
    if (!Number.isFinite(num)) return fallback == null ? 0 : fallback;
    return num;
  }

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function clampText(value, fallback) {
    var text = asText(value);
    return text || fallback || '';
  }

  function tokenFromOptions(options) {
    options = options || {};
    if (typeof options.tokenProvider === 'function') {
      return asText(options.tokenProvider());
    }
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

  function canUseLocalStorage() {
    try {
      return !!window.localStorage;
    } catch (e) {
      return false;
    }
  }

  function buildAgentActivityStreamUrl(options) {
    var baseUrl = baseUrlFromOptions(options);
    var token = tokenFromOptions(options);
    var url = baseUrl + '/api/agents/activity-stream';
    if (token) url += '?token=' + encodeURIComponent(token);
    return url;
  }

  function formatElapsed(raw) {
    var direct = asText(raw.elapsed_label || raw.elapsedLabel);
    if (direct) return direct;
    var ms = asNumber(raw.elapsed_ms != null ? raw.elapsed_ms : raw.elapsedMs, 0);
    if (ms <= 0) return '0.0s';
    return (ms / 1000).toFixed(1) + 's';
  }

  function normalizeAgentActivity(raw) {
    if (raw && raw.data && !raw.agent_role && !raw.agentRole) raw = raw.data;
    raw = raw || {};
    var statusCode = Math.round(asNumber(raw.status_code != null ? raw.status_code : raw.statusCode, 0));
    var ok = raw.ok !== false && (statusCode === 0 || statusCode < 400);
    return {
      id: clampText(raw.id, 'agent-activity-' + Date.now()),
      type: clampText(raw.type, 'agent_call'),
      agentRole: clampText(raw.agent_role || raw.agentRole, ''),
      agentLabel: clampText(raw.agent_label || raw.agentLabel, 'Agent'),
      agentName: clampText(raw.agent_name || raw.agentName, '业务 Agent'),
      agentColor: clampText(raw.agent_color || raw.agentColor, '#2563EB'),
      workflowCode: clampText(raw.workflow_code || raw.workflowCode, 'workflow'),
      workflowLabel: clampText(raw.workflow_label || raw.workflowLabel, '工作流调用'),
      routePath: clampText(raw.route_path || raw.routePath, ''),
      callType: clampText(raw.call_type || raw.callType, 'workflow'),
      knowledgeSource: clampText(raw.knowledge_source || raw.knowledgeSource, '业务知识库'),
      statusCode: statusCode,
      ok: ok,
      elapsedMs: Math.max(0, Math.round(asNumber(raw.elapsed_ms != null ? raw.elapsed_ms : raw.elapsedMs, 0))),
      elapsedLabel: formatElapsed(raw),
      requestId: clampText(raw.request_id || raw.requestId, ''),
      createdAt: clampText(raw.created_at || raw.createdAt, ''),
    };
  }

  function createRoot() {
    var root = document.getElementById(ROOT_ID);
    if (root) return root;

    root = document.createElement('div');
    root.id = ROOT_ID;
    root.className = 'agent-activity-root';
    root.innerHTML =
      '<section class="agent-activity-panel" aria-label="智能体实时调用">' +
        '<header class="agent-activity-head">' +
          '<div class="agent-activity-title">' +
            '<span class="agent-activity-status-dot" data-agent-activity-dot></span>' +
            '<span class="agent-activity-title-full">智能体实时调用</span>' +
            '<span class="agent-activity-title-compact">AI</span>' +
            '<span class="agent-activity-count" data-agent-activity-count hidden>0</span>' +
          '</div>' +
          '<div class="agent-activity-head-actions">' +
            '<span class="agent-activity-status" data-agent-activity-status>连接中</span>' +
            '<button type="button" class="agent-activity-toggle" data-agent-activity-toggle aria-label="收起实时调用面板" title="收起">−</button>' +
          '</div>' +
        '</header>' +
        '<div class="agent-activity-tabs" role="tablist" aria-label="Agent 调用视图" data-agent-activity-tabbar>' +
          '<button type="button" class="agent-activity-tab is-active" role="tab" aria-selected="true" tabindex="0" data-agent-activity-tab="latest">最新</button>' +
          '<button type="button" class="agent-activity-tab" role="tab" aria-selected="false" tabindex="-1" data-agent-activity-tab="flow">链路</button>' +
          '<button type="button" class="agent-activity-tab" role="tab" aria-selected="false" tabindex="-1" data-agent-activity-tab="detail">详情</button>' +
        '</div>' +
        '<div class="agent-activity-body" data-agent-activity-body>' +
          '<section class="agent-activity-tab-panel is-active" role="tabpanel" data-agent-activity-panel="latest" data-agent-activity-scroll="true">' +
            '<div class="agent-activity-feed" data-agent-activity-feed></div>' +
            '<div class="agent-activity-empty" data-agent-activity-empty>等待 Agent 调用</div>' +
          '</section>' +
          '<section class="agent-activity-tab-panel" role="tabpanel" data-agent-activity-panel="flow" data-agent-activity-scroll="true" hidden>' +
            '<div class="agent-activity-flow" data-agent-activity-flow></div>' +
          '</section>' +
          '<section class="agent-activity-tab-panel" role="tabpanel" data-agent-activity-panel="detail" data-agent-activity-scroll="true" hidden>' +
            '<div class="agent-activity-detail" data-agent-activity-detail></div>' +
          '</section>' +
        '</div>' +
        '<div class="agent-activity-live" data-agent-activity-live aria-live="polite"></div>' +
      '</section>';
    document.body.appendChild(root);
    return root;
  }

  function getNodes(root, selector) {
    if (!root || typeof root.querySelectorAll !== 'function') return [];
    return Array.prototype.slice.call(root.querySelectorAll(selector));
  }

  function isKnownTab(tab) {
    return ACTIVITY_TABS.indexOf(tab) >= 0;
  }

  function setActiveTab(state, tab) {
    if (!state || !state.root) return;
    var nextTab = isKnownTab(tab) ? tab : 'latest';
    state.activeTab = nextTab;
    state.root.setAttribute('data-agent-activity-tab', nextTab);

    getNodes(state.root, '[data-agent-activity-tab]').forEach(function (button) {
      var selected = button.getAttribute('data-agent-activity-tab') === nextTab;
      button.classList.toggle('is-active', selected);
      button.setAttribute('aria-selected', selected ? 'true' : 'false');
      button.setAttribute('tabindex', selected ? '0' : '-1');
    });

    getNodes(state.root, '[data-agent-activity-panel]').forEach(function (panel) {
      var selected = panel.getAttribute('data-agent-activity-panel') === nextTab;
      panel.hidden = !selected;
      panel.classList.toggle('is-active', selected);
    });
  }

  function setCollapsedState(state, collapsed, options) {
    if (!state || !state.root) return;
    options = options || {};
    var wasCollapsed = !!state.collapsed;
    var beforeSize = rootSize(state.root);
    var beforeRight = state.position ? state.position.left + beforeSize.width : null;
    state.collapsed = !!collapsed;
    state.root.classList.toggle('is-collapsed', state.collapsed);
    if (state.position && beforeRight != null && wasCollapsed !== state.collapsed) {
      var currentSize = rootSize(state.root);
      applyPosition(state, {
        left: beforeRight - currentSize.width,
        top: state.position.top,
      }, false);
    }
    state.root.classList.remove('is-expanding', 'is-collapsing');
    if (!options.silent) {
      state.root.classList.add(state.collapsed ? 'is-collapsing' : 'is-expanding');
      if (!state.collapsed) {
        setTimeout(function () {
          if (state.root) applyPosition(state, state.position || defaultPosition(state.root), false);
        }, 0);
      }
    }
    clearTimeout(state.transitionTimer);
    state.transitionTimer = setTimeout(function () {
      if (!state.root) return;
      state.root.classList.remove('is-expanding', 'is-collapsing');
      if (state.position) {
        var nextPosition = state.position;
        if (beforeRight != null && wasCollapsed !== state.collapsed) {
          var nextSize = rootSize(state.root);
          nextPosition = {
            left: beforeRight - nextSize.width,
            top: state.position.top,
          };
        }
        applyPosition(state, nextPosition, false);
      }
    }, 260);
    var button = state.root.querySelector('[data-agent-activity-toggle]');
    if (!button) return;
    button.textContent = state.collapsed ? '+' : '−';
    button.setAttribute('aria-label', state.collapsed ? '展开实时调用面板' : '收起实时调用面板');
    button.setAttribute('title', state.collapsed ? '展开' : '收起');
    button.setAttribute('aria-expanded', state.collapsed ? 'false' : 'true');
  }

  function switchTabByDirection(state, direction) {
    if (!state || !state.root || state.root.classList.contains('is-collapsed')) return;
    var currentIndex = ACTIVITY_TABS.indexOf(state.activeTab || 'latest');
    if (currentIndex < 0) currentIndex = 0;
    var nextIndex = (currentIndex + direction + ACTIVITY_TABS.length) % ACTIVITY_TABS.length;
    setActiveTab(state, ACTIVITY_TABS[nextIndex]);
  }

  function findScrollRegion(target, root) {
    var node = target;
    while (node && node !== root) {
      if (node.getAttribute && node.getAttribute('data-agent-activity-scroll') === 'true') return node;
      node = node.parentElement;
    }
    return null;
  }

  function canScrollRegion(region, deltaY) {
    if (!region) return false;
    var scrollHeight = Number(region.scrollHeight || 0);
    var clientHeight = Number(region.clientHeight || 0);
    var scrollTop = Number(region.scrollTop || 0);
    if (scrollHeight <= clientHeight + 1) return false;
    if (deltaY > 0) return scrollTop + clientHeight < scrollHeight - 1;
    if (deltaY < 0) return scrollTop > 1;
    return false;
  }

  function viewportSize() {
    var doc = document.documentElement || {};
    return {
      width: Math.max(window.innerWidth || 0, doc.clientWidth || 0, 320),
      height: Math.max(window.innerHeight || 0, doc.clientHeight || 0, 320),
    };
  }

  function minRootWidth(root) {
    return root && root.classList && root.classList.contains('is-collapsed') ? 82 : 112;
  }

  function rootSize(root) {
    if (!root) return { width: 0, height: 0 };
    var minWidth = minRootWidth(root);
    if (typeof root.getBoundingClientRect === 'function') {
      var rect = root.getBoundingClientRect();
      return {
        width: Math.max(Number(rect.width || 0), Number(root.offsetWidth || 0), minWidth),
        height: Math.max(Number(rect.height || 0), Number(root.offsetHeight || 0), 42),
      };
    }
    return {
      width: Math.max(Number(root.offsetWidth || 0), minRootWidth(root)),
      height: Math.max(Number(root.offsetHeight || 0), 42),
    };
  }

  function clampPosition(root, position) {
    var viewport = viewportSize();
    var size = rootSize(root);
    return {
      left: Math.min(Math.max(Number(position.left || 0), DRAG_EDGE_PADDING), Math.max(DRAG_EDGE_PADDING, viewport.width - size.width - DRAG_EDGE_PADDING)),
      top: Math.min(Math.max(Number(position.top || 0), DRAG_EDGE_PADDING), Math.max(DRAG_EDGE_PADDING, viewport.height - size.height - DRAG_EDGE_PADDING)),
    };
  }

  function defaultPosition(root) {
    var viewport = viewportSize();
    var size = rootSize(root);
    return clampPosition(root, {
      left: viewport.width - size.width - 22,
      top: Math.max(82, DRAG_EDGE_PADDING),
    });
  }

  function savePosition(position) {
    if (!canUseLocalStorage()) return;
    try {
      window.localStorage.setItem(DRAG_STORAGE_KEY, JSON.stringify(position));
    } catch (e) {}
  }

  function loadPosition(root) {
    if (!canUseLocalStorage()) return defaultPosition(root);
    try {
      var raw = window.localStorage.getItem(DRAG_STORAGE_KEY);
      if (!raw) return defaultPosition(root);
      var parsed = JSON.parse(raw);
      return clampPosition(root, parsed || {});
    } catch (e) {
      return defaultPosition(root);
    }
  }

  function applyPosition(state, position, persist) {
    if (!state || !state.root) return;
    var nextPosition = clampPosition(state.root, position || defaultPosition(state.root));
    state.position = nextPosition;
    state.root.style.left = nextPosition.left + 'px';
    state.root.style.top = nextPosition.top + 'px';
    state.root.style.right = 'auto';
    state.root.style.bottom = 'auto';
    state.root.classList.add('is-positioned');
    if (persist) savePosition(nextPosition);
  }

  function setStatus(state, kind, text) {
    if (!state || !state.root) return;
    state.statusKind = kind || 'idle';
    var root = state.root;
    root.setAttribute('data-agent-activity-status', state.statusKind);
    var status = root.querySelector('[data-agent-activity-status]');
    if (status) status.textContent = text || '待命';
  }

  function renderActivityBubble(event, index) {
    var stateClass = event.ok ? 'is-ok' : 'is-error';
    var statusText = event.ok ? '成功' : '异常';
    var requestHtml = event.requestId
      ? '<span>request_id ' + escapeHtml(event.requestId) + '</span>'
      : '';
    return '<article class="agent-activity-bubble ' + stateClass + (index === 0 ? ' is-latest' : '') + '" style="--agent-color:' + escapeHtml(event.agentColor) + '">' +
      '<div class="agent-activity-bubble__head">' +
        '<span class="agent-activity-bubble__mark">' + escapeHtml(event.agentLabel.slice(0, 2) || 'AI') + '</span>' +
        '<div class="agent-activity-bubble__title">' +
          '<strong>' + escapeHtml(event.agentName) + '</strong>' +
          '<span>' + escapeHtml(event.callType) + '</span>' +
        '</div>' +
        '<span class="agent-activity-bubble__state">' + escapeHtml(statusText) + '</span>' +
      '</div>' +
      '<div class="agent-activity-bubble__flow">' +
        '<span class="agent-activity-bubble__code">' + escapeHtml(event.workflowCode) + '</span>' +
        '<span class="agent-activity-bubble__name">' + escapeHtml(event.workflowLabel) + '</span>' +
      '</div>' +
      '<div class="agent-activity-bubble__meta">' +
        '<span>' + escapeHtml(event.elapsedLabel) + '</span>' +
        '<span>' + escapeHtml(event.knowledgeSource) + '</span>' +
        requestHtml +
      '</div>' +
    '</article>';
  }

  function renderFlowItem(event, index) {
    return '<article class="agent-activity-flow-item" style="--agent-color:' + escapeHtml(event.agentColor) + '">' +
      '<span class="agent-activity-flow-item__index">' + escapeHtml(String(index + 1)) + '</span>' +
      '<div class="agent-activity-flow-item__body">' +
        '<strong>' + escapeHtml(event.agentLabel || event.agentName) + '</strong>' +
        '<span>' + escapeHtml(event.workflowCode) + ' · ' + escapeHtml(event.workflowLabel) + '</span>' +
      '</div>' +
      '<span class="agent-activity-flow-item__time">' + escapeHtml(event.elapsedLabel) + '</span>' +
    '</article>';
  }

  function renderLatestDetail(event) {
    if (!event) {
      return '<div class="agent-activity-empty agent-activity-empty--inner">暂无调用详情</div>';
    }
    var rows = [
      ['Agent', event.agentName],
      ['工作流', event.workflowCode + ' · ' + event.workflowLabel],
      ['知识源', event.knowledgeSource],
      ['耗时', event.elapsedLabel],
      ['状态', event.ok ? '成功' : '异常'],
      ['路径', event.routePath || '未记录'],
      ['request_id', event.requestId || '未记录'],
    ];
    return '<dl class="agent-activity-detail-list">' + rows.map(function (row) {
      return '<div class="agent-activity-detail-row">' +
        '<dt>' + escapeHtml(row[0]) + '</dt>' +
        '<dd>' + escapeHtml(row[1]) + '</dd>' +
      '</div>';
    }).join('') + '</dl>';
  }

  function renderEvents(state) {
    if (!state || !state.root) return;
    var feed = state.root.querySelector('[data-agent-activity-feed]');
    var empty = state.root.querySelector('[data-agent-activity-empty]');
    var flow = state.root.querySelector('[data-agent-activity-flow]');
    var detail = state.root.querySelector('[data-agent-activity-detail]');
    var count = state.root.querySelector('[data-agent-activity-count]');
    if (!feed || !empty) return;
    var events = state.events || [];
    var live = state.root.querySelector('[data-agent-activity-live]');
    var latest = events[0] || null;

    if (count) {
      count.hidden = !events.length;
      count.textContent = String(events.length) + '条';
    }
    if (flow) {
      flow.innerHTML = events.length
        ? events.map(renderFlowItem).join('')
        : '<div class="agent-activity-empty agent-activity-empty--inner">暂无调用链路</div>';
    }
    if (detail) {
      detail.innerHTML = renderLatestDetail(latest);
    }

    if (!events.length) {
      feed.innerHTML = '';
      empty.hidden = false;
      state.root.classList.remove('has-agent-activity');
      if (live) live.textContent = '等待 Agent 调用。';
      return;
    }

    empty.hidden = true;
    state.root.classList.add('has-agent-activity');
    feed.innerHTML = events.map(renderActivityBubble).join('');
    if (live && latest) {
      live.textContent = latest.agentName + ' 调用了 ' + latest.workflowCode + '，耗时 ' + latest.elapsedLabel + '。';
    }
  }

  function pushAgentActivityEvent(raw) {
    var state = AgentO._agentActivityState;
    if (!state || !state.root) {
      state = mountAgentActivityPanel({ connect: false });
    }
    var event = normalizeAgentActivity(raw);
    state.events = [event].concat(state.events || []).slice(0, MAX_EVENTS);
    renderEvents(state);
    setStatus(state, 'live', '运行中');
    return event;
  }

  function parseEventPayload(event) {
    try {
      return JSON.parse(event && event.data ? event.data : '{}');
    } catch (e) {
      return {};
    }
  }

  function closeSource(state) {
    if (state && state.source && typeof state.source.close === 'function') {
      state.source.close();
    }
    if (state) state.source = null;
  }

  function scheduleReconnect(state) {
    if (!state || state.destroyed || state.options.connect === false) return;
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = setTimeout(function () {
      connectStream(state);
    }, RECONNECT_DELAY_MS);
  }

  function addSourceListener(source, eventName, handler) {
    if (source && typeof source.addEventListener === 'function') {
      source.addEventListener(eventName, handler);
    } else if (source) {
      source['on' + eventName] = handler;
    }
  }

  function connectStream(state) {
    if (!state || state.destroyed || state.options.connect === false) return;
    closeSource(state);

    if (!window.EventSource) {
      setStatus(state, 'offline', '不支持');
      return;
    }
    if (!tokenFromOptions(state.options)) {
      setStatus(state, 'offline', '未登录');
      return;
    }

    var source = new window.EventSource(buildAgentActivityStreamUrl(state.options));
    state.source = source;
    setStatus(state, 'connecting', '连接中');

    source.onopen = function () {
      setStatus(state, 'live', '运行中');
    };
    addSourceListener(source, 'ready', function () {
      setStatus(state, 'live', '运行中');
    });
    addSourceListener(source, 'heartbeat', function () {
      setStatus(state, 'live', '运行中');
    });
    addSourceListener(source, 'agent_call', function (event) {
      pushAgentActivityEvent(parseEventPayload(event));
    });
    source.onerror = function () {
      if (state.destroyed) return;
      setStatus(state, 'reconnect', '重连中');
      closeSource(state);
      scheduleReconnect(state);
    };
  }

  function syncDockState(state) {
    if (!state || !state.root) return;
    var dhRoot = document.getElementById('dh-floating-root');
    var hasDigitalHuman = !!(dhRoot && !dhRoot.hidden && document.getElementById('dh-widget'));
    state.root.classList.toggle('agent-activity-root--with-digital-human', hasDigitalHuman);
  }

  function bindToggle(state) {
    var button = state.root.querySelector('[data-agent-activity-toggle]');
    if (!button || button.__agentActivityBound) return;
    button.__agentActivityBound = true;
    button.addEventListener('click', function () {
      setCollapsedState(state, !state.root.classList.contains('is-collapsed'));
    });
  }

  function shouldSkipDragTarget(target, root) {
    var node = target;
    while (node && node !== root) {
      if (node.tagName && /^(BUTTON|A|INPUT|TEXTAREA|SELECT)$/.test(String(node.tagName).toUpperCase())) return true;
      if (node.getAttribute && node.getAttribute('data-agent-activity-tab')) return true;
      node = node.parentElement;
    }
    return false;
  }

  function bindDrag(state) {
    if (!state || !state.root || state.root.__agentActivityDragBound) return;
    var handle = state.root.querySelector('.agent-activity-head');
    if (!handle || !handle.addEventListener) return;
    state.root.__agentActivityDragBound = true;

    handle.addEventListener('pointerdown', function (event) {
      if (event.button != null && event.button !== 0) return;
      if (shouldSkipDragTarget(event.target, state.root)) return;
      var position = state.position || defaultPosition(state.root);
      state.drag = {
        pointerId: event.pointerId,
        startX: Number(event.clientX || 0),
        startY: Number(event.clientY || 0),
        left: position.left,
        top: position.top,
        moved: false,
      };
      state.root.classList.add('is-dragging');
      if (handle.setPointerCapture && event.pointerId != null) {
        try {
          handle.setPointerCapture(event.pointerId);
        } catch (e) {}
      }
      if (event.preventDefault) event.preventDefault();
    });

    handle.addEventListener('pointermove', function (event) {
      if (!state.drag) return;
      var dx = Number(event.clientX || 0) - state.drag.startX;
      var dy = Number(event.clientY || 0) - state.drag.startY;
      if (Math.abs(dx) + Math.abs(dy) > 2) state.drag.moved = true;
      applyPosition(state, {
        left: state.drag.left + dx,
        top: state.drag.top + dy,
      }, false);
    });

    function endDrag(event) {
      if (!state.drag) return;
      if (handle.releasePointerCapture && event && event.pointerId != null) {
        try {
          handle.releasePointerCapture(event.pointerId);
        } catch (e) {}
      }
      state.root.classList.remove('is-dragging');
      if (state.position) savePosition(state.position);
      state.drag = null;
    }

    handle.addEventListener('pointerup', endDrag);
    handle.addEventListener('pointercancel', endDrag);
  }

  function bindTabs(state) {
    if (!state || !state.root || state.root.__agentActivityTabsBound) return;
    state.root.__agentActivityTabsBound = true;

    getNodes(state.root, '[data-agent-activity-tab]').forEach(function (button) {
      button.addEventListener('click', function () {
        setCollapsedState(state, false);
        setActiveTab(state, button.getAttribute('data-agent-activity-tab'));
      });
      button.addEventListener('keydown', function (event) {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
        event.preventDefault();
        switchTabByDirection(state, event.key === 'ArrowRight' ? 1 : -1);
      });
    });
  }

  function initializePanelState(state, options) {
    setActiveTab(state, state.activeTab || 'latest');
    if (typeof state.collapsed !== 'boolean') {
      state.collapsed = options.initialCollapsed !== false;
    }
    setCollapsedState(state, state.collapsed, { silent: true });
    if (!state.position) {
      applyPosition(state, loadPosition(state.root), false);
    } else {
      applyPosition(state, state.position, false);
    }
  }

  function syncPanelBindings(state, options) {
    bindToggle(state);
    bindDrag(state);
    bindTabs(state);
    initializePanelState(state, options || {});
  }

  function observeDockState(state) {
    syncDockState(state);
    if (state.observer || !window.MutationObserver || !document.body) return;
    state.observer = new window.MutationObserver(function () {
      syncDockState(state);
    });
    state.observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['hidden', 'class', 'style'],
    });
  }

  function bindViewportClamp(state) {
    if (!state || state.viewportClampBound || !window.addEventListener) return;
    state.viewportClampBound = true;
    state.onViewportClamp = function () {
      if (!state.root || state.destroyed) return;
      applyPosition(state, state.position || defaultPosition(state.root), true);
    };
    window.addEventListener('resize', state.onViewportClamp);
  }

  function mountAgentActivityPanel(options) {
    options = Object.assign({ connect: true }, options || {});
    var state = AgentO._agentActivityState;
    if (!state || state.destroyed) {
      state = {
        root: createRoot(),
        source: null,
        events: [],
        options: options,
        reconnectTimer: null,
        observer: null,
        destroyed: false,
        statusKind: 'idle',
        activeTab: 'latest',
        collapsed: null,
      };
      AgentO._agentActivityState = state;
      syncPanelBindings(state, options);
      renderEvents(state);
    } else {
      state.root = state.root || createRoot();
      state.options = Object.assign({}, state.options || {}, options);
      state.destroyed = false;
      syncPanelBindings(state, state.options);
    }

    observeDockState(state);
    bindViewportClamp(state);
    if (options.connect !== false && !state.source) {
      connectStream(state);
    }
    return state;
  }

  function destroyAgentActivityPanel() {
    var state = AgentO._agentActivityState;
    if (!state) return;
    state.destroyed = true;
    clearTimeout(state.reconnectTimer);
    closeSource(state);
    if (state.observer && typeof state.observer.disconnect === 'function') {
      state.observer.disconnect();
    }
    if (state.onViewportClamp && window.removeEventListener) {
      window.removeEventListener('resize', state.onViewportClamp);
    }
    if (state.root && state.root.parentElement) {
      state.root.parentElement.removeChild(state.root);
    }
    AgentO._agentActivityState = null;
  }

  AgentO.normalizeAgentActivity = normalizeAgentActivity;
  AgentO.renderActivityBubble = renderActivityBubble;
  AgentO.buildAgentActivityStreamUrl = buildAgentActivityStreamUrl;
  AgentO.mountAgentActivityPanel = mountAgentActivityPanel;
  AgentO.destroyAgentActivityPanel = destroyAgentActivityPanel;
  AgentO.pushAgentActivityEvent = pushAgentActivityEvent;
})();
