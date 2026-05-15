const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_JS_PATH = path.join(__dirname, 'js', 'app.js');
const appSource = fs.readFileSync(APP_JS_PATH, 'utf8');

function extractFunction(source, name) {
  const marker = `function ${name}`;
  const start = source.indexOf(marker);
  if (start === -1) {
    throw new Error(`Cannot find ${name} in app.js`);
  }
  const braceStart = source.indexOf('{', start);
  let depth = 0;
  for (let index = braceStart; index < source.length; index += 1) {
    const char = source[index];
    if (char === '{') depth += 1;
    if (char === '}') {
      depth -= 1;
      if (depth === 0) {
        return source.slice(start, index + 1);
      }
    }
  }
  throw new Error(`Cannot extract ${name}`);
}

const context = {
  getComputedStyle: (el) => ({
    overflowY: el.overflowY || 'visible',
    overflow: el.overflow || el.overflowY || 'visible',
  }),
};
vm.createContext(context);
[
  'normalizeMyTaskGroups',
  'flattenPendingTaskGroups',
  'examCenterHasScrollableOverflow',
  'examCenterIsScrollable',
  'examCenterCanScroll',
  'examCenterResolveWheelScrollTarget',
].forEach((name) => {
  vm.runInContext(extractFunction(appSource, name), context);
});

class FakeElement {
  constructor({ classes = [], attrs = {}, scrollHeight = 0, clientHeight = 0, scrollTop = 0, overflowY = 'visible' } = {}) {
    this.classes = new Set(classes);
    this.attrs = { ...attrs };
    this.scrollHeight = scrollHeight;
    this.clientHeight = clientHeight;
    this.scrollTop = scrollTop;
    this.overflowY = overflowY;
    this.children = [];
    this.parentElement = null;
    this.nodeType = 1;
  }

  append(child) {
    child.parentElement = this;
    this.children.push(child);
    return child;
  }

  matches(selector) {
    if (selector === '.assessment-center-paper-card') return this.classes.has('assessment-center-paper-card');
    if (selector === '.assessment-center-paper-questions') return this.classes.has('assessment-center-paper-questions');
    if (selector === '[data-exam-scroll-area]') return Object.prototype.hasOwnProperty.call(this.attrs, 'data-exam-scroll-area');
    if (selector === '[data-exam-scroll-area]:not(.hidden)') {
      return this.matches('[data-exam-scroll-area]') && !this.classes.has('hidden');
    }
    if (selector === '[data-exam-scroll-primary]:not(.hidden)') {
      return Object.prototype.hasOwnProperty.call(this.attrs, 'data-exam-scroll-primary') && !this.classes.has('hidden');
    }
    return false;
  }

  closest(selector) {
    let current = this;
    while (current) {
      if (current.matches(selector)) return current;
      current = current.parentElement;
    }
    return null;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }

  querySelectorAll(selector) {
    const matches = [];
    const visit = (node) => {
      if (node.matches(selector)) matches.push(node);
      node.children.forEach(visit);
    };
    this.children.forEach(visit);
    return matches;
  }
}

function testFlattenPendingTaskGroups() {
  const pending = context.flattenPendingTaskGroups({
    todo: [{ id: 1 }],
    retake: [{ id: 2 }],
    completed: [
      { id: 3, submit_status: 'in_progress', status: 'active', remaining_seconds: 0 },
      { id: 4, submit_status: 'submitted', status: 'active', remaining_seconds: 300 },
    ],
  });

  assert.deepEqual(
    pending.map((item) => item.id),
    [1, 2],
    '已完成分组里的进行中残留记录不应该再回流到当前考试列表'
  );
}

function testPaperWheelPrefersQuestionScroller() {
  const root = new FakeElement({ overflowY: 'hidden' });
  const currentPane = root.append(new FakeElement({ attrs: { 'data-exam-scroll-area': '' }, overflowY: 'auto' }));
  const paperCard = currentPane.append(new FakeElement({ classes: ['assessment-center-paper-card'], overflowY: 'visible' }));
  const questionScroller = paperCard.append(
    new FakeElement({
      classes: ['assessment-center-paper-questions'],
      scrollHeight: 1200,
      clientHeight: 400,
      scrollTop: 20,
      overflowY: 'auto',
    })
  );
  const footer = paperCard.append(new FakeElement());
  const footerButton = footer.append(new FakeElement());

  const resolved = context.examCenterResolveWheelScrollTarget(root, footerButton, 120);
  assert.equal(resolved, questionScroller, '试卷卡片内的滚轮应优先驱动题目滚动区');
}

function testResolverIgnoresVisibleOverflowAncestors() {
  const root = new FakeElement({ overflowY: 'hidden' });
  const currentPane = root.append(
    new FakeElement({
      attrs: { 'data-exam-scroll-area': '' },
      scrollHeight: 900,
      clientHeight: 500,
      overflowY: 'auto',
    })
  );
  const nonScrollableLayout = currentPane.append(
    new FakeElement({
      classes: ['assessment-center-current'],
      scrollHeight: 880,
      clientHeight: 500,
      overflowY: 'visible',
    })
  );
  const child = nonScrollableLayout.append(new FakeElement());

  const resolved = context.examCenterResolveWheelScrollTarget(root, child, 120);
  assert.equal(resolved, currentPane, '不应把 overflow: visible 的布局容器当成滚动目标');
}

testFlattenPendingTaskGroups();
testPaperWheelPrefersQuestionScroller();
testResolverIgnoresVisibleOverflowAncestors();
