const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const appSource = fs.readFileSync(path.join(__dirname, 'js', 'app.js'), 'utf8');
const digitalHumanSource = fs.readFileSync(path.join(__dirname, 'js', 'digital-human.js'), 'utf8');

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

function assertFunctionDoesNotContain(source, name, forbidden) {
  const body = extractFunction(source, name);
  assert.equal(
    body.includes(forbidden),
    false,
    `${name} should not contain ${forbidden}`
  );
}

function assertFunctionContains(source, name, expected) {
  const body = extractFunction(source, name);
  assert.equal(
    body.includes(expected),
    true,
    `${name} should contain ${expected}`
  );
}

const context = {};
vm.createContext(context);
vm.runInContext(extractFunction(digitalHumanSource, '_bubbleLabelForKind'), context);

assert.equal(context._bubbleLabelForKind('quick_query'), '数据查询');
assert.equal(context._bubbleLabelForKind('qa'), '知识问答');

assert.equal(
  appSource.includes('qq-follow-up-caption">你可以继续问'),
  false,
  'quick query should not render the follow-up prompt caption'
);
assert.equal(
  appSource.includes("sales_amount: '销售额'"),
  true,
  'sales_amount should have a business-facing label'
);

assertFunctionContains(appSource, 'quickQuerySpeakResult', "bubbleKind: 'quick_query'");
assertFunctionContains(appSource, 'quickQueryBuildSpeechText', 'quickQueryHumanizeFieldNames(summary)');
assertFunctionContains(appSource, 'submitQueryParse', 'quickQuerySpeakResult');
assertFunctionContains(appSource, 'submitQuickQuerySend', 'quickQuerySpeakResult');
assertFunctionContains(appSource, 'renderQuickQueryPage', 'quickQuerySpeakResult');
assertFunctionDoesNotContain(appSource, 'submitQueryParse', "bubbleKind: 'qa'");
assertFunctionDoesNotContain(appSource, 'renderQuickQueryPage', '_dhVoiceFollowUp');
assertFunctionDoesNotContain(appSource, 'renderQuickQueryPage', 'SpeechRecognition');
assertFunctionDoesNotContain(digitalHumanSource, '_handleQuickAction', 'voiceFollowUp');
assert.equal(
  digitalHumanSource.includes("action: 'voiceFollowUp'"),
  false,
  'quick query should not expose voice follow-up actions'
);
assertFunctionContains(digitalHumanSource, '_getMenuItems', "action: 'newSession'");
assertFunctionContains(digitalHumanSource, '_handleQuickAction', "window.startNewQuickQueryConversation");
assert.equal(
  appSource.includes('window.startNewQuickQueryConversation = startNewQuickQueryConversation'),
  true,
  'quick query new conversation should be callable from the digital human menu'
);
