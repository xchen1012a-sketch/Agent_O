(function () {
  'use strict';

  var AgentO = window.AgentO = window.AgentO || {};

  var DEFAULT_ENTRY = {
    id: 'user_input',
    label: '用户输入',
    agentName: '用户输入',
    headline: '训练任务、顾客问题、经营查询统一进入编排层',
    color: '#334155',
  };

  var NODE_POSITIONS = {
    user_input: { x: 0, y: -165 },
    tutor: { x: -165, y: -45 },
    practice: { x: 0, y: -58 },
    examiner: { x: 165, y: -45 },
    analyst: { x: 0, y: 82 },
    service: { x: 0, y: 205 },
  };

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function asText(value) {
    return String(value == null ? '' : value).trim();
  }

  function asNumber(value, fallback) {
    var num = Number(value);
    if (!Number.isFinite(num)) return fallback == null ? 0 : fallback;
    return num;
  }

  function pickColor(raw, fallback) {
    var value = asText(raw);
    return value || fallback || '#2563eb';
  }

  function normalizeWorkflow(raw) {
    raw = raw || {};
    return {
      code: asText(raw.code),
      label: asText(raw.label || raw.code),
      routePath: asText(raw.route_path || raw.routePath),
      callType: asText(raw.call_type || raw.callType || 'workflow'),
      configured: raw.configured !== false,
      todayCallCount: Math.max(0, Math.round(asNumber(raw.today_call_count != null ? raw.today_call_count : raw.todayCallCount, 0))),
    };
  }

  function normalizeAgent(raw, index) {
    raw = raw || {};
    var id = asText(raw.id || raw.role || raw.agent_role);
    var workflows = Array.isArray(raw.workflows) ? raw.workflows.map(normalizeWorkflow).filter(function (item) {
      return !!item.code;
    }) : [];
    var workflowCount = Math.max(workflows.length, Math.round(asNumber(raw.workflow_count != null ? raw.workflow_count : raw.workflowCount, workflows.length)));
    var configuredCount = raw.configured_workflow_count != null || raw.configuredWorkflowCount != null
      ? Math.round(asNumber(raw.configured_workflow_count != null ? raw.configured_workflow_count : raw.configuredWorkflowCount, 0))
      : workflows.filter(function (item) { return item.configured; }).length;
    var todayCallCount = raw.today_call_count != null || raw.todayCallCount != null
      ? Math.round(asNumber(raw.today_call_count != null ? raw.today_call_count : raw.todayCallCount, 0))
      : workflows.reduce(function (sum, item) { return sum + item.todayCallCount; }, 0);
    var chartWeight = Math.max(2, Math.round(asNumber(raw.chart_weight != null ? raw.chart_weight : raw.chartWeight, todayCallCount || workflowCount * 2 || 2)));
    return {
      id: id || ('agent_' + (index + 1)),
      label: asText(raw.label || raw.name || id),
      agentName: asText(raw.agent_name || raw.agentName || raw.name || raw.label || id),
      headline: asText(raw.headline || raw.tagline),
      responsibility: asText(raw.responsibility || raw.description),
      color: pickColor(raw.color, '#2563eb'),
      workflowCount: workflowCount,
      configuredWorkflowCount: Math.max(0, configuredCount),
      todayCallCount: Math.max(0, todayCallCount),
      chartWeight: chartWeight,
      workflows: workflows,
    };
  }

  function normalizeLink(raw) {
    raw = raw || {};
    return {
      source: asText(raw.source),
      target: asText(raw.target),
      label: asText(raw.label),
      value: Math.max(1, Math.round(asNumber(raw.value, 1))),
    };
  }

  function normalizeSummary(rawSummary, agents) {
    rawSummary = rawSummary || {};
    var workflowCount = agents.reduce(function (sum, item) { return sum + item.workflowCount; }, 0);
    var configuredCount = agents.reduce(function (sum, item) { return sum + item.configuredWorkflowCount; }, 0);
    var todayCallCount = agents.reduce(function (sum, item) { return sum + item.todayCallCount; }, 0);
    return {
      agentCount: Math.round(asNumber(rawSummary.agent_count != null ? rawSummary.agent_count : rawSummary.agentCount, agents.length)),
      workflowCount: Math.round(asNumber(rawSummary.workflow_count != null ? rawSummary.workflow_count : rawSummary.workflowCount, workflowCount)),
      configuredWorkflowCount: Math.round(asNumber(rawSummary.configured_workflow_count != null ? rawSummary.configured_workflow_count : rawSummary.configuredWorkflowCount, configuredCount)),
      todayCallCount: Math.round(asNumber(rawSummary.today_call_count != null ? rawSummary.today_call_count : rawSummary.todayCallCount, todayCallCount)),
      hiddenWorkflowCount: Math.round(asNumber(rawSummary.hidden_workflow_count != null ? rawSummary.hidden_workflow_count : rawSummary.hiddenWorkflowCount, 0)),
      updatedAt: asText(rawSummary.updated_at || rawSummary.updatedAt),
    };
  }

  function normalizeEntry(raw) {
    raw = raw || {};
    return {
      id: asText(raw.id) || DEFAULT_ENTRY.id,
      label: asText(raw.label) || DEFAULT_ENTRY.label,
      agentName: asText(raw.agent_name || raw.agentName) || DEFAULT_ENTRY.agentName,
      headline: asText(raw.headline) || DEFAULT_ENTRY.headline,
      color: pickColor(raw.color, DEFAULT_ENTRY.color),
    };
  }

  function normalizeAgentTopology(raw) {
    if (raw && raw.data && Array.isArray(raw.data.agents)) raw = raw.data;
    raw = raw || {};
    var agents = Array.isArray(raw.agents) ? raw.agents.map(normalizeAgent).filter(function (item) {
      return !!item.id;
    }) : [];
    var links = Array.isArray(raw.links) ? raw.links.map(normalizeLink).filter(function (item) {
      return !!item.source && !!item.target;
    }) : [];
    return {
      entry: normalizeEntry(raw.entry),
      agents: agents,
      links: links,
      summary: normalizeSummary(raw.summary, agents),
    };
  }

  function selectedAgent(topology, selectedId) {
    if (!topology.agents.length) return null;
    for (var i = 0; i < topology.agents.length; i++) {
      if (topology.agents[i].id === selectedId) return topology.agents[i];
    }
    return topology.agents[0];
  }

  function formatCount(value) {
    var num = Math.max(0, Math.round(asNumber(value, 0)));
    return String(num);
  }

  function nodeSize(agent) {
    if (!agent || agent.id === 'user_input') return 70;
    return Math.max(68, Math.min(104, 62 + agent.chartWeight * 4));
  }

  function prefersReducedMotion() {
    try {
      return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    } catch (e) {
      return false;
    }
  }

  function graphNodes(topology, selectedId) {
    var nodes = [
      { id: '__pad_left', name: '', x: -280, y: 0, symbolSize: 1, silent: true, label: { show: false }, itemStyle: { opacity: 0 } },
      { id: '__pad_right', name: '', x: 280, y: 0, symbolSize: 1, silent: true, label: { show: false }, itemStyle: { opacity: 0 } },
      { id: '__pad_top', name: '', x: 0, y: -230, symbolSize: 1, silent: true, label: { show: false }, itemStyle: { opacity: 0 } },
      { id: '__pad_bottom', name: '', x: 0, y: 260, symbolSize: 1, silent: true, label: { show: false }, itemStyle: { opacity: 0 } },
    {
      id: topology.entry.id,
      name: topology.entry.label,
      labelText: topology.entry.label,
      agentName: topology.entry.agentName,
      headline: topology.entry.headline,
      roleId: topology.entry.id,
      x: NODE_POSITIONS.user_input.x,
      y: NODE_POSITIONS.user_input.y,
      symbolSize: nodeSize({ id: 'user_input' }),
      value: 1,
      itemStyle: {
        color: topology.entry.color,
        borderColor: '#ffffff',
        borderWidth: 3,
        shadowColor: 'rgba(51, 65, 85, 0.22)',
        shadowBlur: 18,
      },
    }];
    topology.agents.forEach(function (agent) {
      var pos = NODE_POSITIONS[agent.id] || { x: 0, y: 0 };
      var selected = selectedId === agent.id;
      nodes.push({
        id: agent.id,
        name: agent.agentName,
        labelText: agent.label,
        agentName: agent.agentName,
        headline: agent.headline,
        roleId: agent.id,
        workflowCount: agent.workflowCount,
        todayCallCount: agent.todayCallCount,
        x: pos.x,
        y: pos.y,
        symbolSize: nodeSize(agent),
        value: agent.chartWeight,
        itemStyle: {
          color: agent.color,
          borderColor: selected ? '#0f172a' : '#ffffff',
          borderWidth: selected ? 4 : 2,
          shadowColor: agent.color.replace(')', ', 0.22)').replace('rgb', 'rgba'),
          shadowBlur: selected ? 28 : 16,
        },
      });
    });
    return nodes;
  }

  function buildAgentGraphOption(data, selectedId) {
    var topology = normalizeAgentTopology(data);
    var selected = selectedAgent(topology, selectedId);
    var activeId = selected ? selected.id : '';
    var reduced = prefersReducedMotion();
    return {
      animation: !reduced,
      animationDuration: reduced ? 0 : 460,
      animationDurationUpdate: reduced ? 0 : 360,
      animationEasingUpdate: 'cubicOut',
      tooltip: {
        trigger: 'item',
        borderWidth: 0,
        backgroundColor: 'rgba(15, 23, 42, 0.92)',
        textStyle: { color: '#fff', fontSize: 12 },
        formatter: function (params) {
          var d = params.data || {};
          if (params.dataType === 'edge') {
            return escapeHtml(d.label || '协作链路') + '<br/>频度：' + escapeHtml(formatCount(d.value || 1));
          }
          if (d.roleId === topology.entry.id) {
            return escapeHtml(d.agentName || d.name) + '<br/>' + escapeHtml(d.headline || '');
          }
          return escapeHtml(d.agentName || d.name) +
            '<br/>工作流：' + escapeHtml(formatCount(d.workflowCount || 0)) +
            '<br/>今日调用：' + escapeHtml(formatCount(d.todayCallCount || 0));
        },
      },
      series: [{
        type: 'graph',
        layout: 'none',
        roam: true,
        draggable: false,
        left: 20,
        right: 20,
        top: 20,
        bottom: 20,
        data: graphNodes(topology, activeId),
        links: topology.links.map(function (link) {
          return {
            source: link.source,
            target: link.target,
            label: link.label,
            value: link.value,
            lineStyle: {
              width: Math.max(2, Math.min(8, link.value)),
              color: '#94a3b8',
              opacity: 0.78,
              curveness: link.source === 'service' ? 0.28 : 0.16,
            },
          };
        }),
        edgeSymbol: ['none', 'arrow'],
        edgeSymbolSize: [0, 10],
        label: {
          show: true,
          color: '#ffffff',
          fontSize: 13,
          fontWeight: 700,
          formatter: function (params) {
            return params.data && params.data.labelText ? params.data.labelText : params.name;
          },
        },
        edgeLabel: {
          show: true,
          color: '#475569',
          fontSize: 11,
          formatter: function (params) {
            return params.data && params.data.label ? params.data.label : '';
          },
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: {
            opacity: 0.95,
          },
        },
      }],
      aria: {
        enabled: true,
      },
    };
  }

  function renderStat(label, value, sub) {
    return '<div class="agent-orchestration-stat">' +
      '<div class="agent-orchestration-stat__value">' + escapeHtml(value) + '</div>' +
      '<div class="agent-orchestration-stat__label">' + escapeHtml(label) + '</div>' +
      (sub ? '<div class="agent-orchestration-stat__sub">' + escapeHtml(sub) + '</div>' : '') +
      '</div>';
  }

  function renderWorkflowRow(item) {
    var stateClass = item.configured ? 'is-on' : 'is-off';
    var stateText = item.configured ? '已配置' : '未配置';
    return '<li class="agent-orchestration-workflow">' +
      '<div class="agent-orchestration-workflow__main">' +
        '<span class="agent-orchestration-workflow__code">' + escapeHtml(item.code) + '</span>' +
        '<span class="agent-orchestration-workflow__name">' + escapeHtml(item.label) + '</span>' +
      '</div>' +
      '<div class="agent-orchestration-workflow__meta">' +
        '<span>' + escapeHtml(item.callType || 'workflow') + '</span>' +
        '<span class="agent-orchestration-workflow__state ' + stateClass + '">' + escapeHtml(stateText) + '</span>' +
      '</div>' +
    '</li>';
  }

  function renderAgentDetail(container, topology, selectedId) {
    var detail = container.querySelector ? container.querySelector('[data-agent-detail]') : null;
    if (!detail) return;
    var agent = selectedAgent(topology, selectedId);
    if (!agent) {
      detail.innerHTML = '<div class="agent-orchestration-detail-empty">暂无 Agent 数据</div>';
      return;
    }
    var workflows = agent.workflows.map(renderWorkflowRow).join('');
    detail.innerHTML =
      '<div class="agent-orchestration-detail-head">' +
        '<div class="agent-orchestration-detail-mark" style="--agent-color:' + escapeHtml(agent.color) + '">' + escapeHtml(agent.label.slice(0, 2)) + '</div>' +
        '<div class="agent-orchestration-detail-title">' +
          '<div class="agent-orchestration-detail-kicker">当前节点</div>' +
          '<h3>' + escapeHtml(agent.agentName) + '</h3>' +
          '<p>' + escapeHtml(agent.headline) + '</p>' +
        '</div>' +
      '</div>' +
      '<div class="agent-orchestration-detail-copy">' + escapeHtml(agent.responsibility) + '</div>' +
      '<div class="agent-orchestration-detail-metrics">' +
        renderStat('工作流', formatCount(agent.workflowCount), '') +
        renderStat('已配置', formatCount(agent.configuredWorkflowCount), '') +
        renderStat('今日调用', formatCount(agent.todayCallCount), '') +
      '</div>' +
      '<ul class="agent-orchestration-workflows">' + workflows + '</ul>';
  }

  function syncAgentButtons(container, selectedId) {
    if (!container.querySelectorAll) return;
    var buttons = container.querySelectorAll('[data-agent-node-button]');
    buttons.forEach(function (button) {
      var on = button.getAttribute('data-agent-role-id') === selectedId;
      button.classList.toggle('is-active', on);
      button.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function updateSelection(container, topology, chart, selectedId) {
    var agent = selectedAgent(topology, selectedId);
    if (!agent) return;
    if (container.dataset) container.dataset.agentOrchestrationSelected = agent.id;
    renderAgentDetail(container, topology, agent.id);
    syncAgentButtons(container, agent.id);
    var summary = container.querySelector ? container.querySelector('[data-agent-a11y]') : null;
    if (summary) {
      summary.textContent = agent.agentName + '，包含 ' + agent.workflowCount + ' 条工作流，今日调用 ' + agent.todayCallCount + ' 次。';
    }
    if (chart && typeof chart.setOption === 'function') {
      chart.setOption(buildAgentGraphOption(topology, agent.id), true);
    }
  }

  function renderAgentButton(agent, selectedId) {
    return '<button type="button" class="agent-orchestration-agent-button' +
      (agent.id === selectedId ? ' is-active' : '') +
      '" data-agent-node-button data-agent-role-id="' + escapeHtml(agent.id) +
      '" aria-pressed="' + (agent.id === selectedId ? 'true' : 'false') + '">' +
        '<span class="agent-orchestration-agent-button__dot" style="--agent-color:' + escapeHtml(agent.color) + '"></span>' +
        '<span>' + escapeHtml(agent.agentName) + '</span>' +
      '</button>';
  }

  function renderFrame(container, topology, selectedId) {
    var summary = topology.summary;
    var buttons = topology.agents.map(function (agent) {
      return renderAgentButton(agent, selectedId);
    }).join('');
    container.className = 'agent-orchestration-root';
    container.innerHTML =
      '<section class="agent-orchestration-shell" aria-labelledby="agent-orchestration-title">' +
        '<header class="agent-orchestration-head">' +
          '<div>' +
            '<div class="agent-orchestration-kicker">智能体编排</div>' +
            '<h2 id="agent-orchestration-title" class="agent-orchestration-title">5-Agent 协作图</h2>' +
            '<p class="agent-orchestration-subtitle">从成长计划、实战陪练、上岗考核到经营分析和一线支持，14 条核心工作流被编排为 5 个业务 Agent。</p>' +
          '</div>' +
          '<div class="agent-orchestration-badge">Agent-Orchestrated Training OS</div>' +
        '</header>' +
        '<div class="agent-orchestration-stats">' +
          renderStat('Agent', formatCount(summary.agentCount), '业务角色') +
          renderStat('工作流', formatCount(summary.workflowCount), '核心编排') +
          renderStat('已配置', formatCount(summary.configuredWorkflowCount), 'Dify 应用') +
          renderStat('今日调用', formatCount(summary.todayCallCount), '接口请求') +
        '</div>' +
        '<div class="agent-orchestration-layout">' +
          '<div class="agent-orchestration-chart-panel">' +
            '<div class="agent-orchestration-chart" data-agent-graph role="img" aria-label="5-Agent 协作拓扑图"></div>' +
            '<div class="agent-orchestration-a11y" data-agent-a11y aria-live="polite"></div>' +
          '</div>' +
          '<aside class="agent-orchestration-detail" data-agent-detail aria-live="polite"></aside>' +
        '</div>' +
        '<div class="agent-orchestration-agent-strip" role="group" aria-label="Agent 节点">' + buttons + '</div>' +
      '</section>';
  }

  function renderLoading(container) {
    container.className = 'agent-orchestration-root';
    container.innerHTML =
      '<section class="agent-orchestration-shell agent-orchestration-shell--loading">' +
        '<div class="agent-orchestration-head">' +
          '<div><div class="agent-orchestration-kicker">智能体编排</div><h2 class="agent-orchestration-title">5-Agent 协作图</h2></div>' +
        '</div>' +
        '<div class="agent-orchestration-loading-grid">' +
          '<div></div><div></div><div></div><div></div>' +
        '</div>' +
        '<div class="agent-orchestration-loading-body"></div>' +
      '</section>';
    return { topology: null, option: null, chart: null, pending: true };
  }

  function renderEmpty(container, message) {
    container.className = 'agent-orchestration-root';
    container.innerHTML =
      '<section class="agent-orchestration-shell agent-orchestration-shell--empty">' +
        '<div class="agent-orchestration-head">' +
          '<div><div class="agent-orchestration-kicker">智能体编排</div><h2 class="agent-orchestration-title">5-Agent 协作图</h2></div>' +
        '</div>' +
        '<div class="agent-orchestration-empty">' + escapeHtml(message || '暂无 Agent 拓扑数据') + '</div>' +
      '</section>';
    return { topology: null, option: null, chart: null };
  }

  function initChart(container, topology, selectedId) {
    var chartEl = container.querySelector ? container.querySelector('[data-agent-graph]') : null;
    if (!chartEl || !window.echarts || typeof window.echarts.init !== 'function') return null;
    if (container.__agentOrchestrationChart && typeof container.__agentOrchestrationChart.dispose === 'function') {
      container.__agentOrchestrationChart.dispose();
    }
    var chart = window.echarts.init(chartEl);
    var option = buildAgentGraphOption(topology, selectedId);
    chart.setOption(option, true);
    if (chart.on) {
      chart.on('click', function (params) {
        var roleId = params && params.data && params.data.roleId ? String(params.data.roleId) : '';
        if (!roleId || roleId === topology.entry.id) return;
        updateSelection(container, topology, chart, roleId);
      });
    }
    container.__agentOrchestrationChart = chart;
    if (!container.__agentOrchestrationResizeBound && window.addEventListener) {
      window.addEventListener('resize', function () {
        if (container.__agentOrchestrationChart && typeof container.__agentOrchestrationChart.resize === 'function') {
          container.__agentOrchestrationChart.resize();
        }
      });
      container.__agentOrchestrationResizeBound = true;
    }
    return chart;
  }

  function bindButtons(container, topology, chart) {
    if (!container.querySelectorAll) return;
    var buttons = container.querySelectorAll('[data-agent-node-button]');
    buttons.forEach(function (button) {
      button.addEventListener('click', function () {
        updateSelection(container, topology, chart, button.getAttribute('data-agent-role-id') || '');
      });
    });
  }

  function renderWithData(container, data) {
    var topology = normalizeAgentTopology(data);
    if (!topology.agents.length) return renderEmpty(container, '暂无 Agent 拓扑数据');
    var selectedId = container.dataset && container.dataset.agentOrchestrationSelected
      ? container.dataset.agentOrchestrationSelected
      : topology.agents[0].id;
    var active = selectedAgent(topology, selectedId) || topology.agents[0];
    renderFrame(container, topology, active.id);
    var chart = initChart(container, topology, active.id);
    renderAgentDetail(container, topology, active.id);
    syncAgentButtons(container, active.id);
    bindButtons(container, topology, chart);
    updateSelection(container, topology, chart, active.id);
    return { topology: topology, option: buildAgentGraphOption(topology, active.id), chart: chart };
  }

  function renderAgentOrchestration(container, props) {
    if (!container) return null;
    if (props && (Array.isArray(props.agents) || (props.data && Array.isArray(props.data.agents)))) {
      return renderWithData(container, props.data || props);
    }
    props = props || {};
    if (typeof props.apiFetch === 'function') {
      var requestKey = String(Date.now());
      if (container.dataset) container.dataset.agentOrchestrationRequest = requestKey;
      renderLoading(container);
      props.apiFetch().then(function (data) {
        if (container.dataset && container.dataset.agentOrchestrationRequest !== requestKey) return;
        renderWithData(container, data || {});
      }).catch(function (err) {
        if (container.dataset && container.dataset.agentOrchestrationRequest !== requestKey) return;
        renderEmpty(container, (err && err.message) || 'Agent 拓扑加载失败');
      });
      return { topology: null, option: null, chart: null, pending: true };
    }
    return renderEmpty(container, '暂无 Agent 拓扑数据');
  }

  AgentO.normalizeAgentTopology = normalizeAgentTopology;
  AgentO.buildAgentGraphOption = buildAgentGraphOption;
  AgentO.renderAgentOrchestration = renderAgentOrchestration;
})();
