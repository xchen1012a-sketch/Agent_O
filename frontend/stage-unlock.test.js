const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE_PATH = path.join(__dirname, 'js', 'stage-unlock.js');

function createElement(tagName) {
  return {
    tagName: String(tagName || '').toUpperCase(),
    className: '',
    innerHTML: '',
    attributes: {},
    children: [],
    parentNode: null,
    classList: {
      add() {},
      remove() {},
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
    },
    appendChild(child) {
      child.parentNode = this;
      this.children.push(child);
      return child;
    },
    remove() {
      if (!this.parentNode) return;
      this.parentNode.children = this.parentNode.children.filter((item) => item !== this);
      this.parentNode = null;
    },
  };
}

const body = createElement('body');
const listeners = {};
const timeouts = [];
const context = {
  window: { AgentO: {} },
  document: {
    body,
    createElement,
    addEventListener(type, handler) {
      listeners[type] = handler;
    },
    querySelector(selector) {
      if (selector !== '.stage-unlock-overlay') return null;
      return body.children.find((child) => String(child.className || '').includes('stage-unlock-overlay')) || null;
    },
  },
  setTimeout(fn) {
    timeouts.push(fn);
    return timeouts.length;
  },
  clearTimeout() {},
};
context.window.window = context.window;
context.window.document = context.document;
context.window.setTimeout = context.setTimeout;
context.window.clearTimeout = context.clearTimeout;

vm.createContext(context);
vm.runInContext(fs.readFileSync(SOURCE_PATH, 'utf8'), context);

assert.equal(typeof context.window.AgentO.renderStageUnlock, 'function');
assert.equal(typeof listeners['agento:stage-unlock'], 'function');

const first = context.window.AgentO.renderStageUnlock(body, {
  stage: 2,
  name: '销售转化与上岗',
  passed_stage_name: '基础认知',
  review_score: 86.5,
});

assert.ok(first, 'renderStageUnlock should return the overlay element');
assert.equal(body.children.length, 1, 'one overlay should be mounted');
assert.equal(first.attributes.role, 'alert');
assert.equal(first.innerHTML.includes('销售转化与上岗'), true);
assert.equal(first.innerHTML.includes('86.5'), true);

const second = context.window.AgentO.renderStageUnlock(body, {
  stage: 3,
  name: '独立上岗',
});

assert.equal(body.children.length, 1, 'rendering again should replace the existing overlay');
assert.equal(body.children[0], second);
assert.equal(second.innerHTML.includes('独立上岗'), true);
