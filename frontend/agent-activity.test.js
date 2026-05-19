const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE_PATH = path.join(__dirname, 'js', 'agent-activity.js');
const APP_SOURCE_PATH = path.join(__dirname, 'js', 'app.js');

function createClassList() {
  const values = new Set();
  return {
    add(name) { values.add(name); },
    remove(name) { values.delete(name); },
    contains(name) { return values.has(name); },
    toggle(name, force) {
      const next = force === undefined ? !values.has(name) : !!force;
      if (next) values.add(name);
      else values.delete(name);
      return next;
    },
    toString() { return Array.from(values).join(' '); },
  };
}

class ElementStub {
  constructor(tagName) {
    this.tagName = tagName;
    this.id = '';
    this.hidden = false;
    this.parentElement = null;
    this.children = [];
    this.attributes = {};
    this.style = {};
    this.classList = createClassList();
    this._innerHTML = '';
    this._selectors = {};
    this.textContent = '';
    this.offsetWidth = 120;
    this.offsetHeight = 42;
  }

  set className(value) {
    this._className = value;
    this.classList = createClassList();
    String(value || '').split(/\s+/).filter(Boolean).forEach((item) => this.classList.add(item));
  }

  get className() {
    return this._className || this.classList.toString();
  }

  set innerHTML(value) {
    this._innerHTML = String(value || '');
    if (this._innerHTML.includes('data-agent-activity-feed')) {
      this._selectors['[data-agent-activity-status]'] = new ElementStub('span');
      this._selectors['[data-agent-activity-feed]'] = new ElementStub('div');
      this._selectors['[data-agent-activity-empty]'] = new ElementStub('div');
      this._selectors['[data-agent-activity-live]'] = new ElementStub('div');
      this._selectors['[data-agent-activity-toggle]'] = new ElementStub('button');
      this._selectors['[data-agent-activity-count]'] = new ElementStub('span');
      this._selectors['[data-agent-activity-flow]'] = new ElementStub('div');
      this._selectors['[data-agent-activity-detail]'] = new ElementStub('div');
      this._selectors['[data-agent-activity-body]'] = new ElementStub('div');
      this._selectors['.agent-activity-head'] = new ElementStub('header');

      const tabs = ['latest', 'flow', 'detail'].map((name) => {
        const tab = new ElementStub('button');
        tab.setAttribute('data-agent-activity-tab', name);
        return tab;
      });
      const panels = ['latest', 'flow', 'detail'].map((name) => {
        const panel = new ElementStub('section');
        panel.setAttribute('data-agent-activity-panel', name);
        return panel;
      });
      this._selectorAll = {
        '[data-agent-activity-tab]': tabs,
        '[data-agent-activity-panel]': panels,
      };
    }
  }

  get innerHTML() {
    return this._innerHTML;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return this.attributes[name] || '';
  }

  appendChild(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    this.children = this.children.filter((item) => item !== child);
    child.parentElement = null;
    return child;
  }

  querySelector(selector) {
    return this._selectors[selector] || null;
  }

  querySelectorAll(selector) {
    return (this._selectorAll && this._selectorAll[selector]) || [];
  }

  addEventListener(eventName, handler) {
    this._listeners = this._listeners || {};
    this._listeners[eventName] = handler;
  }

  dispatch(eventName, event) {
    if (this._listeners && this._listeners[eventName]) {
      this._listeners[eventName](event || {});
    }
  }

  getBoundingClientRect() {
    return {
      width: this.offsetWidth,
      height: this.offsetHeight,
      left: parseFloat(this.style.left || '0'),
      top: parseFloat(this.style.top || '0'),
      right: parseFloat(this.style.left || '0') + this.offsetWidth,
      bottom: parseFloat(this.style.top || '0') + this.offsetHeight,
    };
  }

  setPointerCapture() {}

  releasePointerCapture() {}
}

function createDocument() {
  const body = new ElementStub('body');
  const document = {
    body,
    createElement(tagName) {
      return new ElementStub(tagName);
    },
    getElementById(id) {
      const stack = [body].concat(body.children);
      while (stack.length) {
        const item = stack.shift();
        if (item.id === id) return item;
        stack.push(...item.children);
      }
      return null;
    },
  };
  return document;
}

function FakeEventSource(url) {
  this.url = url;
  this.closed = false;
  this.listeners = {};
  FakeEventSource.instances.push(this);
}
FakeEventSource.instances = [];
FakeEventSource.prototype.addEventListener = function addEventListener(name, handler) {
  this.listeners[name] = handler;
};
FakeEventSource.prototype.close = function close() {
  this.closed = true;
};
FakeEventSource.prototype.emit = function emit(name, data) {
  if (name === 'open' && typeof this.onopen === 'function') this.onopen({ data: data || '' });
  if (this.listeners[name]) this.listeners[name]({ data: JSON.stringify(data || {}) });
};

const document = createDocument();
const storage = {};
const localStorage = {
  getItem(key) {
    if (key === 'token') return 'local-token';
    return storage[key] || '';
  },
  setItem(key, value) {
    storage[key] = String(value);
  },
};
const context = {
  window: {
    AgentO: {},
    document,
    localStorage,
    EventSource: FakeEventSource,
    innerWidth: 1280,
    innerHeight: 720,
    addEventListener() {},
    removeEventListener() {},
  },
  document,
  console,
  setTimeout,
  clearTimeout,
};
context.window.window = context.window;

vm.createContext(context);
vm.runInContext(fs.readFileSync(SOURCE_PATH, 'utf8'), context);

const AgentO = context.window.AgentO;
const appSource = fs.readFileSync(APP_SOURCE_PATH, 'utf8');

assert.equal(typeof AgentO.mountAgentActivityPanel, 'function');
assert.equal(typeof AgentO.pushAgentActivityEvent, 'function');
assert.equal(typeof AgentO.normalizeAgentActivity, 'function');
assert.ok(
  appSource.includes("const AGENT_ACTIVITY_ORDINARY_PAGES = ['practical_training', 'on_duty_assistant', 'knowledge_qa', 'quick_query'];"),
  'ordinary users should only see the activity panel on business AI pages'
);
assert.ok(
  appSource.includes("window.AgentO.mountAgentActivityPanel({ connect: false });"),
  'ordinary users should mount the activity panel without global SSE'
);
assert.ok(
  appSource.includes('maybePushLocalAgentActivityEvent(path, method, res.status, json'),
  'ordinary user activity events should be generated from local apiFetch responses'
);

const normalized = AgentO.normalizeAgentActivity({
  agent_role: 'practice',
  agent_name: '陪练 Agent',
  workflow_code: 'practice1',
  workflow_label: '实战对练',
  elapsed_ms: 1234,
});
assert.equal(normalized.agentName, '陪练 Agent');
assert.equal(normalized.workflowCode, 'practice1');
assert.equal(normalized.elapsedLabel, '1.2s');

const streamUrl = AgentO.buildAgentActivityStreamUrl({
  baseUrl: 'http://127.0.0.1:8002',
  tokenProvider: () => 'a b',
});
assert.equal(streamUrl, 'http://127.0.0.1:8002/api/agents/activity-stream?token=a%20b');

const state = AgentO.mountAgentActivityPanel({ baseUrl: '', token: 'abc' });
assert.ok(document.getElementById('agent-activity-root'));
assert.equal(state.root.classList.contains('is-collapsed'), true);
assert.equal(state.root.getAttribute('data-agent-activity-tab'), 'latest');
assert.equal(state.root.style.right, 'auto');
assert.equal(FakeEventSource.instances.length, 1);
assert.equal(FakeEventSource.instances[0].url, '/api/agents/activity-stream?token=abc');

FakeEventSource.instances[0].emit('agent_call', {
  agent_label: '陪练',
  agent_name: '陪练 Agent',
  workflow_code: 'practice1',
  workflow_label: '实战对练',
  knowledge_source: '销售话术库',
  elapsed_ms: 980,
  request_id: 'req-1',
});

const feed = state.root.querySelector('[data-agent-activity-feed]');
const status = state.root.querySelector('[data-agent-activity-status]');
const flow = state.root.querySelector('[data-agent-activity-flow]');
const detail = state.root.querySelector('[data-agent-activity-detail]');
assert.match(feed.innerHTML, /陪练 Agent/);
assert.match(feed.innerHTML, /practice1/);
assert.match(flow.innerHTML, /practice1/);
assert.match(detail.innerHTML, /request_id/);
assert.equal(status.textContent, '运行中');

const toggle = state.root.querySelector('[data-agent-activity-toggle]');
toggle.dispatch('click');
assert.equal(state.root.classList.contains('is-collapsed'), false);
state.root.offsetWidth = 360;
state.root.querySelector('[data-agent-activity-body]').dispatch('wheel', {
  deltaY: 120,
  target: state.root,
  preventDefault() {
    this.defaultPrevented = true;
  },
});
assert.equal(state.root.getAttribute('data-agent-activity-tab'), 'latest');

const head = state.root.querySelector('.agent-activity-head');
const startLeft = parseFloat(state.root.style.left || '0');
const startTop = parseFloat(state.root.style.top || '0');
head.dispatch('pointerdown', {
  button: 0,
  pointerId: 1,
  clientX: 100,
  clientY: 100,
  target: head,
  preventDefault() {},
});
head.dispatch('pointermove', {
  pointerId: 1,
  clientX: 140,
  clientY: 125,
  target: head,
});
head.dispatch('pointerup', {
  pointerId: 1,
  target: head,
});
assert.equal(parseFloat(state.root.style.left || '0'), 908);
assert.equal(parseFloat(state.root.style.top || '0'), startTop + 25);
assert.match(storage.agent_activity_position_v1, /"left"/);

const expandedRight = parseFloat(state.root.style.left || '0') + state.root.offsetWidth;
toggle.dispatch('click');
assert.equal(state.root.classList.contains('is-collapsed'), true);
assert.equal(parseFloat(state.root.style.left || '0') + state.root.offsetWidth, expandedRight);

for (let i = 0; i < 5; i += 1) {
  AgentO.pushAgentActivityEvent({
    agent_name: '分析师 Agent',
    workflow_code: 'query' + i,
    workflow_label: '查询',
  });
}
assert.equal(AgentO._agentActivityState.events.length, 4);

AgentO.destroyAgentActivityPanel();
assert.equal(FakeEventSource.instances[0].closed, true);
assert.equal(document.getElementById('agent-activity-root'), null);

const sourceCountBeforeLocalMount = FakeEventSource.instances.length;
const localState = AgentO.mountAgentActivityPanel({ connect: false, token: 'abc' });
assert.ok(document.getElementById('agent-activity-root'));
assert.equal(FakeEventSource.instances.length, sourceCountBeforeLocalMount);
AgentO.pushAgentActivityEvent({
  agent_name: 'Practice Agent',
  workflow_code: 'practice1',
  workflow_label: 'Practice Chat',
  elapsed_ms: 321,
});
assert.equal(localState.events[0].workflowCode, 'practice1');
AgentO.destroyAgentActivityPanel();
