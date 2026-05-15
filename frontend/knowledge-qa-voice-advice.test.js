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

const context = {};
vm.createContext(context);
[
  'normalizeKnowledgeQaSpeechText',
  'isKnowledgeQaVoiceAdviceUsable',
  'extractKnowledgeQaSpeechSummary',
  'resolveKnowledgeQaSpeechPayload',
].forEach((name) => {
  vm.runInContext(extractFunction(appSource, name), context);
});

assert.equal(
  context.isKnowledgeQaVoiceAdviceUsable('黄金更保值，白银更适合日常佩戴。'),
  true,
  'complete knowledge summary should remain usable'
);

assert.equal(
  context.isKnowledgeQaVoiceAdviceUsable('银白硬平价'),
  false,
  'tag-like slogan should be rejected'
);

assert.equal(
  context.isKnowledgeQaVoiceAdviceUsable('先记住75%这个数字'),
  false,
  'memory prompt without independent meaning should be rejected'
);

assert.equal(
  context.extractKnowledgeQaSpeechSummary('黄金（足金）延展性更好，也更保值。白银颜色更清亮，价格门槛更低。'),
  '黄金延展性更好，也更保值。',
  'summary should prefer the first complete sentence and remove parenthetical text'
);

assert.equal(
  context.extractKnowledgeQaSpeechSummary('GIA切工主要看比例、对称性和抛光，这三项共同影响火彩和亮度，所以切工等级不能只看一个参数。'),
  'GIA切工主要看比例、对称性和抛光，这三项共同影响火彩和亮度。',
  'long answer should be condensed into a speakable first-sentence summary'
);

const workflowVoice = context.resolveKnowledgeQaSpeechPayload(
  '黄金延展性更好，也更保值。白银颜色更清亮，价格门槛更低。',
  { voice_advice: '黄金更保值，白银更适合日常佩戴。' }
);
assert.equal(workflowVoice.speechText, '黄金更保值，白银更适合日常佩戴。');
assert.equal(workflowVoice.bubbleKind, 'qa_feedback');
assert.equal(workflowVoice.pose, 'encourage');

const fallbackVoice = context.resolveKnowledgeQaSpeechPayload(
  '黄金延展性更好，也更保值。白银颜色更清亮，价格门槛更低。',
  { voice_advice: '银白硬平价' }
);
assert.equal(fallbackVoice.speechText, '黄金延展性更好，也更保值。');
assert.equal(fallbackVoice.bubbleKind, 'qa');
assert.equal(fallbackVoice.pose, '');

const emptyVoice = context.resolveKnowledgeQaSpeechPayload(
  '参数、比例、对称性、抛光',
  { voice_advice: '先记住这个数字' }
);
assert.equal(emptyVoice.speechText, '');
assert.equal(emptyVoice.bubbleKind, 'qa');
assert.equal(emptyVoice.pose, '');

console.log('knowledge-qa-voice-advice.test.js passed');
