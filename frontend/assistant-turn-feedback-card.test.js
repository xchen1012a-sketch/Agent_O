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
  escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  },
};

vm.createContext(context);
vm.runInContext(extractFunction(appSource, 'renderAssistantTurnFeedbackCard'), context);

const html = context.renderAssistantTurnFeedbackCard({
  intent_label: '\u9700\u6c42\u786e\u8ba4',
  customer_state: '\u89c2\u671b',
  mentor_comment: '\u5148\u6536\u7a84\u9700\u6c42\uff0c\u518d\u7ed9\u65b9\u6848\u3002',
  next_action: '\u786e\u8ba4\u81ea\u7528\u8fd8\u662f\u9001\u793c\u3002',
  next_question: '\u662f\u7ed9\u81ea\u5df1\u9009\u8fd8\u662f\u9001\u4eba\uff1f',
  risk_flag: '\u4e0d\u8981\u76f4\u63a5\u627f\u8bfa\u4fdd\u503c\u56de\u8d2d\u3002',
});

assert.equal(html.includes('background:#FFFBF0'), false, 'yellow inline card style should be removed');
assert.equal(html.includes('border-left:3px solid'), false, 'old colored left bars should be removed');
assert.equal(html.includes('assistant-turn-feedback-card__section'), true, 'minimal section structure should render');
assert.equal(html.includes('assistant-turn-feedback-card__risk'), true, 'risk block should still render');
assert.equal(html.includes('\u662f\u7ed9\u81ea\u5df1\u9009\u8fd8\u662f\u9001\u4eba\uff1f'), true, 'recommended phrase should remain visible');

const minimalHtml = context.renderAssistantTurnFeedbackCard({
  next_question: '\u5148\u786e\u8ba4\u9884\u7b97\u8303\u56f4\u3002',
});

assert.equal(minimalHtml.includes('assistant-turn-feedback-card'), true, 'card should render when only next question exists');
