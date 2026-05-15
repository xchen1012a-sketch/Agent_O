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
  escapeJs(value) {
    return String(value || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
  },
};

vm.createContext(context);
[
  'splitKnowledgeQaLongParagraph',
  'splitKnowledgeQaAnswerBlocks',
  'renderKnowledgeQaAnswerBlocks',
  'renderKnowledgeQaAssistantCard',
].forEach((name) => {
  vm.runInContext(extractFunction(appSource, name), context);
});

const followupHtml = context.renderKnowledgeQaAssistantCard({
  content: '\u57f9\u80b2\u94bb\u9700\u8981\u5982\u5b9e\u8bf4\u660e\u6765\u6e90\u548c\u68c0\u6d4b\u53e3\u5f84\u3002',
  coachQuestion: '\u57f9\u80b2\u94bb\u548c\u5929\u7136\u94bb\u600e\u4e48\u89c4\u8303\u8868\u8fbe\uff1f',
  relatedQuestions: ['\u95e8\u5e97\u5408\u89c4\u8bdd\u672f\u8fb9\u754c\u6709\u54ea\u4e9b\uff1f'],
});

assert.equal(
  followupHtml.includes('\u57f9\u80b2\u94bb\u548c\u5929\u7136\u94bb\u600e\u4e48\u89c4\u8303\u8868\u8fbe\uff1f'),
  false,
  'coachQuestion should not be rendered in plain reply mode'
);
assert.equal(
  followupHtml.includes('\u95e8\u5e97\u5408\u89c4\u8bdd\u672f\u8fb9\u754c\u6709\u54ea\u4e9b\uff1f'),
  true,
  'workflow related questions should still render'
);
assert.equal(
  followupHtml.includes('\u4f60\u8fd8\u53ef\u4ee5\u7ee7\u7eed\u95ee'),
  true,
  'related question title should remain'
);

const plainHtml = context.renderKnowledgeQaAssistantCard({
  content: '\u4f60\u597d\uff0c\u6211\u53ef\u4ee5\u5e2e\u4f60\u89e3\u7b54\u4ea7\u54c1\u77e5\u8bc6\u3001\u9500\u552e\u8bdd\u672f\u3001\u5408\u89c4\u8fb9\u754c\u548c\u7cfb\u7edf\u4f7f\u7528\u95ee\u9898\u3002',
  answerBrief: '\u5148\u7ed9\u7ed3\u8bba\uff1a\u8fd9\u91cc\u5148\u5224\u65ad\u5173\u952e\u5b9a\u4e49\uff0c\u518d\u5f80\u4e0b\u5c55\u5f00\u3002',
  answerReason: '\u56e0\u4e3a\u5148\u8bf4\u7ed3\u8bba\uff0c\u987e\u5ba2\u548c\u5b66\u5458\u90fd\u4f1a\u66f4\u5bb9\u6613\u8ddf\u4e0a\u540e\u9762\u7684\u89e3\u91ca\u3002',
  answerExample: '\u95e8\u5e97\u91cc\u53ef\u4ee5\u8fd9\u6837\u8bf4\uff1a\u5148\u5224\u65ad\uff0c\u518d\u89e3\u91ca\uff0c\u6700\u540e\u8865\u4e00\u4e2a\u95e8\u5e97\u91cc\u7684\u8bf4\u6cd5\u3002',
});

assert.equal(
  plainHtml.includes('\u4f60\u597d\uff0c\u6211\u53ef\u4ee5\u5e2e\u4f60\u89e3\u7b54\u4ea7\u54c1\u77e5\u8bc6'),
  true,
  'plain answer text should render directly'
);
assert.equal(
  plainHtml.includes('\u6211\u5efa\u8bae\u5148\u8fd9\u6837\u7406\u89e3'),
  false,
  'plain reply should not prepend teacher-mode brief text'
);
assert.equal(
  plainHtml.includes('\u8fd9\u6837\u5224\u65ad'),
  false,
  'plain reply should not prepend teacher-mode reason text'
);
assert.equal(
  plainHtml.includes('\u5982\u679c\u4f60\u8981\u5728\u95e8\u5e97\u91cc\u76f4\u63a5\u8868\u8fbe'),
  false,
  'plain reply should not prepend teacher-mode example text'
);
