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
  'createEmptyAssessmentCoach',
  'normalizeAssessmentCoach',
  'buildAssessmentCoachSignature',
  'shouldEmitAssessmentCoach',
  'buildAssessmentStuckCoach',
  'buildAssessmentTimePressureCoach',
  'buildAssessmentResultDebrief',
].forEach((name) => {
  vm.runInContext(extractFunction(appSource, name), context);
});

function testNormalizeAssessmentCoachDefaults() {
  const coach = context.normalizeAssessmentCoach({
    hint_text: '先接住顾虑，再讲证据依据。',
    should_speak: 1,
  });

  assert.equal(coach.phase, '');
  assert.equal(coach.intent_label, '');
  assert.equal(coach.pose, 'think');
  assert.equal(coach.urgency, 'normal');
  assert.equal(coach.should_speak, true);
}

function testAssessmentCoachDedupesSameSignatureWithinCooldown() {
  const session = {
    lastCoachSignature: 'opening|需求确认|先问对象和预算，再继续推荐。|normal',
    lastCoachAt: 1000,
  };
  const coach = context.normalizeAssessmentCoach({
    phase: 'opening',
    intent_label: '需求确认',
    hint_text: '先问对象和预算，再继续推荐。',
    urgency: 'normal',
  });

  assert.equal(context.shouldEmitAssessmentCoach(session, coach, 2000, 5000), false);
  assert.equal(context.shouldEmitAssessmentCoach(session, coach, 7001, 5000), true);
}

function testBuildAssessmentStuckCoachPrefersShortSubmitStrategy() {
  const coach = context.buildAssessmentStuckCoach('short_submit');

  assert.equal(coach.phase, 'stuck');
  assert.equal(coach.should_speak, true);
  assert.ok(coach.hint_text.includes('先补'));
}

function testBuildAssessmentTimePressureCoachUsesTwoThresholds() {
  const fiveMinuteCoach = context.buildAssessmentTimePressureCoach(5 * 60 * 1000);
  const twoMinuteCoach = context.buildAssessmentTimePressureCoach(2 * 60 * 1000);

  assert.equal(fiveMinuteCoach.phase, 'time_pressure');
  assert.equal(fiveMinuteCoach.should_speak, false);
  assert.equal(fiveMinuteCoach.urgency, 'time_pressure');
  assert.equal(twoMinuteCoach.should_speak, true);
  assert.ok(twoMinuteCoach.hint_text.includes('最后'));
}

function testBuildAssessmentResultDebriefReturnsThreePartReview() {
  const coach = context.buildAssessmentResultDebrief({
    score: 82,
    is_pass: 0,
    reply: '这轮接待有亮点，但收口动作不够明确。',
  });

  assert.equal(coach.phase, 'result_debrief');
  assert.equal(coach.should_speak, true);
  assert.equal(coach.intent_label, '考后复盘');
  assert.ok(coach.hint_text.includes('先复盘'));
}

testNormalizeAssessmentCoachDefaults();
testAssessmentCoachDedupesSameSignatureWithinCooldown();
testBuildAssessmentStuckCoachPrefersShortSubmitStrategy();
testBuildAssessmentTimePressureCoachUsesTwoThresholds();
testBuildAssessmentResultDebriefReturnsThreePartReview();
