const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE_PATH = path.join(__dirname, 'js', 'employee-journey.js');

function createElementStub(tagName) {
  return {
    tagName: String(tagName || '').toUpperCase(),
    className: '',
    innerHTML: '',
    textContent: '',
    attributes: {},
    dataset: {},
    style: {},
    children: [],
    parentElement: null,
    classList: {
      add() {},
      remove() {},
      toggle() {},
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      return child;
    },
    removeChild(child) {
      this.children = this.children.filter((item) => item !== child);
      child.parentElement = null;
    },
    remove() {
      if (this.parentElement) this.parentElement.removeChild(this);
    },
    focus() {},
    querySelector() {
      return null;
    },
  };
}

function createContainer() {
  return {
    className: '',
    innerHTML: '',
    dataset: {},
    __listeners: {},
    querySelector(selector) {
      if (selector === '[data-employee-journey-modal-root]') return null;
      return null;
    },
    addEventListener(eventName, handler) {
      this.__listeners[eventName] = handler;
    },
  };
}

const payload = {
  employee: {
    id: '2',
    name: '赵景行',
    role_label: '导购',
    store_name: '广州天河精品店',
    mentor_name: '陈志明',
  },
  summary: {
    total_days: 14,
    start_score: 38,
    current_score: 85,
    score_delta: 47,
    high_risk_count: 2,
    passed: true,
  },
  dimensions: [
    { key: 'product_knowledge', label: '产品知识' },
    { key: 'compliance_expression', label: '合规表达' },
    { key: 'needs_discovery', label: '需求挖掘' },
    { key: 'sales_expression', label: '销售沟通' },
    { key: 'objection_handling', label: '异议处理' },
    { key: 'closing_skill', label: '成交收口' },
  ],
  nodes: [
    {
      day_index: 1,
      title: '入职建档',
      subtitle: '建立员工档案与能力基线',
      score: 38,
      risk_level: 'high',
      stage_no: 1,
      cycle_day_index: 1,
      module_name: '产品基础',
      summary: '入营基线综合 38 分。',
      key_event: true,
      ability_values: { product_knowledge: 42, compliance_expression: 40, needs_discovery: 39, sales_expression: 38, objection_handling: 36, closing_skill: 37 },
      details: { tasks: [], practice: null, assessment: null, learning: null },
    },
    {
      day_index: 7,
      title: '阶段评估未通过',
      subtitle: '风险红灯触发补强',
      score: 58,
      risk_level: 'high',
      stage_no: 1,
      cycle_day_index: 7,
      module_name: '异议处理',
      summary: '阶段评估未通过。',
      key_event: true,
      ability_values: { product_knowledge: 62, compliance_expression: 60, needs_discovery: 59, sales_expression: 58, objection_handling: 56, closing_skill: 57 },
      details: { tasks: [], practice: { score: 58, coach_summary: '价格异议处理偏弱。' }, assessment: { score: 58, is_pass: false }, learning: null },
    },
    {
      day_index: 14,
      title: '阶段晋级',
      subtitle: '通过上岗',
      score: 85,
      risk_level: 'low',
      stage_no: 2,
      cycle_day_index: 7,
      module_name: '独立上岗',
      summary: '通过上岗。',
      key_event: true,
      passed: true,
      ability_values: { product_knowledge: 89, compliance_expression: 87, needs_discovery: 86, sales_expression: 85, objection_handling: 83, closing_skill: 84 },
      details: { tasks: [], practice: null, assessment: null, learning: null },
    },
  ],
};

const context = {
  window: {
    AgentO: {},
    matchMedia() {
      return { matches: false };
    },
    setTimeout(fn) {
      fn();
      return 1;
    },
  },
  document: {
    body: createElementStub('body'),
    createElement: createElementStub,
    addEventListener() {},
  },
  console,
};
context.window.window = context.window;
context.window.document = context.document;

vm.createContext(context);
vm.runInContext(fs.readFileSync(SOURCE_PATH, 'utf8'), context);

assert.equal(typeof context.window.AgentO.renderEmployeeJourney, 'function');
assert.equal(typeof context.window.AgentO.normalizeEmployeeJourneyPayload, 'function');

const normalized = context.window.AgentO.normalizeEmployeeJourneyPayload(payload);
assert.equal(normalized.nodes.length, 3);
assert.equal(normalized.summary.scoreDelta, 47);
assert.equal(normalized.nodes[1].riskLevel, 'high');
assert.equal(normalized.nodes[2].passed, true);

const container = createContainer();
const result = context.window.AgentO.renderEmployeeJourney(container, payload);
assert.equal(result.nodes.length, 3);
assert.ok(container.innerHTML.includes('赵景行的成长之旅'));
assert.ok(container.innerHTML.includes('Day 7'));
assert.ok(container.innerHTML.includes('风险红灯'));
assert.ok(container.innerHTML.includes('综合 85 分'));
assert.ok(container.innerHTML.includes('data-journey-node-index="1"'));
assert.equal(typeof container.__listeners.click, 'function');

container.__listeners.click({
  target: {
    closest(selector) {
      if (selector === '[data-journey-node-index]') {
        return { getAttribute: () => '1' };
      }
      return null;
    },
  },
});
assert.equal(context.document.body.children.length, 1);
assert.ok(context.document.body.children[0].innerHTML.includes('价格异议处理偏弱'));

const empty = createContainer();
context.window.AgentO.renderEmployeeJourney(empty, { employee: { name: '赵景行' }, nodes: [] });
assert.ok(empty.innerHTML.includes('暂无成长轨迹'));

const asyncContainer = createContainer();
let fetchCalled = false;
context.window.AgentO.renderEmployeeJourney(asyncContainer, {
  employeeId: '2',
  apiFetch(opts) {
    fetchCalled = opts.employeeId === '2';
    return Promise.resolve(payload);
  },
});
assert.equal(fetchCalled, true);
assert.ok(asyncContainer.innerHTML.includes('加载成长轨迹中'));
