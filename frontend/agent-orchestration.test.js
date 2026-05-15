const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE_PATH = path.join(__dirname, 'js', 'agent-orchestration.js');

function createContainer() {
  const chartEl = {
    className: 'agent-orchestration-chart',
    attributes: {},
    style: {},
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
  const detailEl = { innerHTML: '' };
  const a11yEl = { textContent: '' };
  return {
    className: '',
    innerHTML: '',
    dataset: {},
    __chartEl: chartEl,
    __detailEl: detailEl,
    __a11yEl: a11yEl,
    querySelector(selector) {
      if (selector === '[data-agent-graph]') return chartEl;
      if (selector === '[data-agent-detail]') return detailEl;
      if (selector === '[data-agent-a11y]') return a11yEl;
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
}

let latestOption = null;
let latestClickHandler = null;
const context = {
  window: {
    AgentO: {},
    matchMedia() {
      return { matches: false };
    },
    addEventListener() {},
  },
  document: {},
  console,
};
context.window.window = context.window;
context.window.document = context.document;
context.window.echarts = {
  init() {
    return {
      setOption(option) {
        latestOption = option;
      },
      on(eventName, handler) {
        if (eventName === 'click') latestClickHandler = handler;
      },
      resize() {},
      dispose() {},
    };
  },
};

vm.createContext(context);
vm.runInContext(fs.readFileSync(SOURCE_PATH, 'utf8'), context);

assert.equal(typeof context.window.AgentO.renderAgentOrchestration, 'function');
assert.equal(typeof context.window.AgentO.buildAgentGraphOption, 'function');

const topology = {
  entry: { id: 'user_input', label: '用户输入', agent_name: '用户输入', color: '#334155' },
  agents: [
    {
      id: 'tutor',
      label: '导师',
      agent_name: '导师 Agent',
      headline: '规划 14 天成长路径',
      responsibility: '生成成长计划。',
      color: '#2563EB',
      workflow_count: 2,
      configured_workflow_count: 2,
      today_call_count: 3,
      workflows: [
        { code: 'growth1', label: '成长计划生成', route_path: '/api/growth/plan', call_type: 'workflow', configured: true },
        { code: 'growth2', label: '成长学习评估', route_path: '/api/growth/evaluate', call_type: 'workflow', configured: true },
      ],
    },
    {
      id: 'practice',
      label: '陪练',
      agent_name: '陪练 Agent',
      headline: '陪员工练到会为止',
      responsibility: '对练、评分、更新能力。',
      color: '#0F766E',
      workflow_count: 3,
      configured_workflow_count: 3,
      today_call_count: 7,
      workflows: [
        { code: 'practice1', label: '实战对练', route_path: '/api/practice/chat', call_type: 'chat', configured: true },
      ],
    },
    { id: 'examiner', label: '考官', agent_name: '考官 Agent', color: '#7C3AED', workflow_count: 4, workflows: [] },
    { id: 'service', label: '客服', agent_name: '客服 Agent', color: '#DB2777', workflow_count: 2, workflows: [] },
    { id: 'analyst', label: '分析师', agent_name: '分析师 Agent', color: '#D97706', workflow_count: 3, workflows: [] },
  ],
  links: [
    { source: 'user_input', target: 'tutor', label: '成长目标', value: 3 },
    { source: 'practice', target: 'analyst', label: '能力更新', value: 6 },
  ],
  summary: { agent_count: 5, workflow_count: 14, configured_workflow_count: 10, today_call_count: 10 },
};

const normalized = context.window.AgentO.normalizeAgentTopology(topology);
assert.equal(normalized.summary.agentCount, 5);
assert.equal(normalized.summary.workflowCount, 14);
assert.equal(normalized.agents[1].agentName, '陪练 Agent');

const option = context.window.AgentO.buildAgentGraphOption(topology, 'practice');
assert.equal(option.series[0].type, 'graph');
assert.equal(option.series[0].data.filter((item) => !item.id.startsWith('__pad_')).length, 6);
assert.equal(option.series[0].links.length, 2);
assert.equal(option.aria.enabled, true);

const container = createContainer();
const result = context.window.AgentO.renderAgentOrchestration(container, { data: topology });
assert.equal(result.topology.summary.agentCount, 5);
assert.match(container.innerHTML, /5-Agent 协作图/);
assert.match(container.innerHTML, /14 条核心工作流/);
assert.ok(latestOption);
assert.equal(latestOption.series[0].data.filter((item) => !item.id.startsWith('__pad_')).length, 6);
assert.equal(typeof latestClickHandler, 'function');

latestClickHandler({ data: { roleId: 'practice' } });
assert.match(container.__detailEl.innerHTML, /陪练 Agent/);
assert.match(container.__detailEl.innerHTML, /practice1/);
assert.equal(container.dataset.agentOrchestrationSelected, 'practice');

const empty = createContainer();
context.window.AgentO.renderAgentOrchestration(empty, { data: { agents: [] } });
assert.match(empty.innerHTML, /暂无 Agent 拓扑数据/);
