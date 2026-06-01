const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const APP_JS_PATH = path.join(__dirname, 'js', 'app.js');
const appSource = fs.readFileSync(APP_JS_PATH, 'utf8');

function extractFunction(source, name) {
  const marker = `function ${name}`;
  const start = source.indexOf(marker);
  if (start === -1) throw new Error(`Cannot find ${name}`);
  const braceStart = source.indexOf('{', start);
  let depth = 0;
  for (let index = braceStart; index < source.length; index += 1) {
    const char = source[index];
    if (char === '{') depth += 1;
    if (char === '}') {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`Cannot extract ${name}`);
}

const context = {
  window: {
    AgentO: {
      renderEvoFeedbackHtml(id) {
        return `<div class="evo-feedback-controls" data-evo-episode-id="${id}">feedback</div>`;
      },
    },
  },
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
  sanitizeQuickQueryRowsForDisplay(rows) {
    return Array.isArray(rows) ? rows : [];
  },
};

vm.createContext(context);
[
  'splitKnowledgeQaLongParagraph',
  'splitKnowledgeQaAnswerBlocks',
  'renderKnowledgeQaAnswerBlocks',
  'renderKnowledgeQaAssistantCard',
  'quickQueryResultPreview',
  'quickQueryReplyTextFromSummary',
  'quickQueryBuildContext',
  'normalizeUnifiedQuickQueryResult',
].forEach((name) => {
  vm.runInContext(extractFunction(appSource, name), context);
});

const qaHtml = context.renderKnowledgeQaAssistantCard({
  content: '知识问答回答',
  evo_episode_id: 42,
});
assert.match(qaHtml, /evo-feedback-controls/);
assert.match(qaHtml, /data-evo-episode-id="42"/);

const quick = context.normalizeUnifiedQuickQueryResult({
  ask_id: 'q-1',
  query_text: '全系统有多少员工？',
  reply_text: '当前系统共有 120 名员工。',
  route_type: 'local_template',
  result_rows: [{ total_count: 120 }],
  evo_episode_id: 43,
});
assert.equal(quick.parseResult.evo_episode_id, 43);
assert.equal(quick.summarizeResult.evo_episode_id, 43);

assert.equal(
  appSource.includes('res.data.evo_episode_id || null'),
  true,
  'assistant and qa message history should keep evo episode ids from API responses'
);
assert.equal(
  appSource.includes('(s.summarizeResult && s.summarizeResult.evo_episode_id) || (s.parseResult && s.parseResult.evo_episode_id) || null'),
  true,
  'quick query message history should keep evo episode ids from API responses'
);
