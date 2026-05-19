const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appSource = fs.readFileSync(path.join(__dirname, 'js', 'app.js'), 'utf8');

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

function assertFunctionContains(name, expected) {
  const body = extractFunction(appSource, name);
  assert.equal(body.includes(expected), true, `${name} should contain ${expected}`);
}

assert.equal(
  appSource.includes("var ASSISTANT_SESSION_KEY = 'assistant_session_v1';"),
  true,
  'assistant session storage key should be declared'
);

assertFunctionContains('loadAssistantSessionState', 'sessionStorage.getItem(assistantStorageKey())');
assertFunctionContains('saveAssistantSessionState', 'sessionStorage.setItem(assistantStorageKey()');
assertFunctionContains('submitAssistantReply', 'saveAssistantSessionState();');
assertFunctionContains('clearAssistantSession', 'saveAssistantSessionState();');
assertFunctionContains('renderAssistantPage', 'loadAssistantSessionState();');

assertFunctionContains('loadQuickQuerySessionState', 'sessionStorage.getItem(quickQueryStorageKey())');
assertFunctionContains('saveQuickQuerySessionState', 'sessionStorage.setItem(quickQueryStorageKey()');

console.log('conversation-persistence.test.js passed');
