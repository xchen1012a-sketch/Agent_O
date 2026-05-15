const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE_PATH = path.join(__dirname, 'js', 'radar-timeline.js');

function createContainer() {
  const chartEl = {
    className: 'radar-timeline-chart',
    attributes: {},
    style: {},
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
  return {
    className: '',
    innerHTML: '',
    dataset: {},
    __chartEl: chartEl,
    querySelector(selector) {
      if (selector === '[data-radar-timeline-chart]') return chartEl;
      return null;
    },
  };
}

let latestOption = null;
let latestTimelineHandler = null;
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
        if (eventName === 'timelinechanged') latestTimelineHandler = handler;
      },
      resize() {},
      dispose() {},
    };
  },
};

vm.createContext(context);
vm.runInContext(fs.readFileSync(SOURCE_PATH, 'utf8'), context);

assert.equal(typeof context.window.AgentO.renderRadarTimeline, 'function');
assert.equal(typeof context.window.AgentO.buildRadarTimelineOption, 'function');

const items = [
  {
    label: 'S1 · Day 1',
    overall_score: 38,
    values: {
      product_knowledge: 42,
      compliance_expression: 40,
      needs_discovery: 35,
      sales_expression: 36,
      objection_handling: 33,
      closing_skill: 39,
    },
  },
  {
    label: 'S3 · Day 14',
    overall_score: 85,
    module_name: '成交收口',
    values: {
      product_knowledge: 88,
      compliance_expression: 86,
      needs_discovery: 83,
      sales_expression: 84,
      objection_handling: 80,
      closing_skill: 87,
    },
  },
];

const option = context.window.AgentO.buildRadarTimelineOption({ items });
assert.equal(option.baseOption.timeline.data.length, 2);
assert.equal(option.baseOption.timeline.currentIndex, 0);
assert.equal(option.baseOption.radar.indicator.length, 6);
assert.deepEqual(Array.from(option.options[0].series[0].data[0].value), [42, 40, 35, 36, 33, 39]);
assert.deepEqual(Array.from(option.options[1].series[0].data[0].value), [88, 86, 83, 84, 80, 87]);

const container = createContainer();
const result = context.window.AgentO.renderRadarTimeline(container, {
  items,
  userName: '赵景行',
});

assert.equal(result.items.length, 2);
assert.ok(container.innerHTML.includes('雷达图跃迁'));
assert.ok(container.innerHTML.includes('赵景行'));
assert.ok(latestOption, 'renderRadarTimeline should initialize ECharts and set an option');
assert.equal(latestOption.baseOption.timeline.autoPlay, true);
assert.equal(typeof latestTimelineHandler, 'function');

const empty = createContainer();
context.window.AgentO.renderRadarTimeline(empty, { items: [] });
assert.ok(empty.innerHTML.includes('暂无能力轨迹'));

const direct = createContainer();
const directResult = context.window.AgentO.renderRadarTimeline(direct, items);
assert.equal(directResult.items.length, 2);
assert.ok(direct.innerHTML.includes('雷达图跃迁'));
