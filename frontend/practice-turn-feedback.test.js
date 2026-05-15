const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');

const ROOT = __dirname;
const BROWSER_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\chrome.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
];

function createStaticServer(rootDir) {
  return http.createServer((req, res) => {
    const urlPath = decodeURIComponent((req.url || '/').split('?')[0]);
    const relativePath = urlPath === '/' ? '/index.html' : urlPath;
    const resolvedPath = path.resolve(rootDir, '.' + relativePath);

    if (!resolvedPath.startsWith(rootDir)) {
      res.writeHead(403);
      res.end('Forbidden');
      return;
    }

    let filePath = resolvedPath;
    if (fs.existsSync(filePath) && fs.statSync(filePath).isDirectory()) {
      filePath = path.join(filePath, 'index.html');
    }
    if (!fs.existsSync(filePath)) {
      res.writeHead(404);
      res.end('Not found');
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    const types = {
      '.html': 'text/html; charset=utf-8',
      '.js': 'application/javascript; charset=utf-8',
      '.css': 'text/css; charset=utf-8',
      '.json': 'application/json; charset=utf-8',
      '.svg': 'image/svg+xml',
      '.png': 'image/png',
      '.jpg': 'image/jpeg',
      '.jpeg': 'image/jpeg',
      '.glb': 'model/gltf-binary',
      '.bin': 'application/octet-stream',
    };

    res.writeHead(200, { 'Content-Type': types[ext] || 'application/octet-stream' });
    fs.createReadStream(filePath).pipe(res);
  });
}

function resolveBrowserExecutable() {
  for (let i = 0; i < BROWSER_CANDIDATES.length; i += 1) {
    if (fs.existsSync(BROWSER_CANDIDATES[i])) return BROWSER_CANDIDATES[i];
  }
  return chromium.executablePath();
}

async function bootstrapPracticePage(page, url) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'dev-token');
    localStorage.setItem('role', 'admin');
    localStorage.setItem('username', 'admin');
    localStorage.setItem('displayName', 'Admin');
    localStorage.setItem('userId', '1');
    localStorage.setItem('store_id', 'store-1');
    localStorage.setItem('store_name', '娴嬭瘯闂ㄥ簵');
    localStorage.setItem(
      'digital_human_prefs_v1:1',
      JSON.stringify({
        auto_voice_enabled: true,
        scenes: {
          practice: false,
          assistant: true,
          knowledge_qa: true,
        },
      })
    );

    const turnFeedback1 = {
      intent_label: '浠锋牸璇曟帰',
      intent_reason: 'Customer is repeatedly probing price, so value perception is still unresolved.',
      customer_state: '璋ㄦ厧',
      mentor_comment: 'Do not rush to discount. Clarify value and usage context first.',
      next_action: 'Confirm whether the customer is stuck on budget or value.',
      next_question: '鎮ㄧ幇鍦ㄦ洿鎷呭績瓒呴绠楋紝杩樻槸瑙夊緱杩欐浠峰€兼劅杩樹笉澶熸槑纭紵',
      voice_advice: 'Hold the price first and confirm whether the hesitation is about budget or value.',
      risk_flag: 'Avoid promising lowest price or guarantee without basis.',
    };
    const turnFeedback2 = {
      intent_label: '鐘硅鲍瑙傛湜',
      intent_reason: 'The customer is delaying the decision rather than rejecting directly.',
      customer_state: '瑙傛湜',
      mentor_comment: 'Stop stacking selling points and surface the real blocker first.',
      next_action: 'Ask what is hardest to decide right now.',
      next_question: 'What are you still unsure about most: budget, style, or gifting scenario?',
      voice_advice: 'Ask for the real hesitation point before pushing forward.',
    };

    window.__practiceSendCount = 0;
    window.__practiceChatCalls = [];
    window.__practiceChatResponses = [
      {
        code: 200,
        message: 'success',
        data: {
          session_id: 'ps_ui_001',
          scene_code: 'objection_handling',
          module_code: 'objection_handling',
          difficulty_level: 'standard',
          assistant_reply: 'I just want to know why this one is so expensive.',
          conversation_id: 'conv_ui_1',
          round_count: 1,
          stage: 'opening',
          end_flag: 0,
          risk_hit_count: 0,
          conversation: [
            { role: 'user', content: 'Why is this one so expensive?', round: 1 },
            { role: 'assistant', content: 'I just want to know why this one is so expensive.', round: 1, turn_feedback: turnFeedback1 },
          ],
          turn_feedback: turnFeedback1,
        },
        meta: {
          workflow_code: 'practice1',
          mock: false,
        },
      },
      {
        code: 200,
        message: 'success',
        data: {
          session_id: 'ps_ui_001',
          scene_code: 'objection_handling',
          module_code: 'objection_handling',
          difficulty_level: 'standard',
          assistant_reply: 'Let me think about it again. I do not want to decide today.',
          conversation_id: 'conv_ui_1',
          round_count: 2,
          stage: 'in_progress',
          end_flag: 0,
          risk_hit_count: 0,
          conversation: [
            { role: 'user', content: 'Why is this one so expensive?', round: 1 },
            { role: 'assistant', content: 'I just want to know why this one is so expensive.', round: 1, turn_feedback: turnFeedback1 },
            { role: 'user', content: 'Let me think about it again.', round: 2 },
            { role: 'assistant', content: 'Let me think about it again. I do not want to decide today.', round: 2, turn_feedback: turnFeedback2 },
          ],
          turn_feedback: turnFeedback2,
        },
        meta: {
          workflow_code: 'practice1',
          mock: false,
        },
      },
    ];

    window.__digitalHumanSystemSettings = {
      enabled: true,
      tts_provider: 'minimax',
      minimax_configured: true,
    };

    const nativeFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === 'string' ? input : String(input && input.url || '');
      if (url.includes('/api/me')) {
        return new Response(
          JSON.stringify({
            code: 200,
            message: 'success',
            data: {
              user_id: '1',
              username: 'admin',
              display_name: 'Admin',
              role: 'admin',
              store_id: 'store-1',
              store_name: '娴嬭瘯闂ㄥ簵',
            },
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.includes('/api/system-settings/digital-human')) {
        return new Response(
          JSON.stringify({
            code: 200,
            message: 'success',
            data: window.__digitalHumanSystemSettings,
          }),
          { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
      }
      if (url.includes('/api/practice/chat')) {
        const body = JSON.parse(String(init && init.body || '{}'));
        window.__practiceChatCalls.push(body);
        if (body.action === 'resume') {
          const latest = window.__practiceChatResponses[Math.max(0, window.__practiceSendCount - 1)];
          return new Response(JSON.stringify(latest), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        }
        const idx = window.__practiceSendCount++;
        const payload = window.__practiceChatResponses[idx];
        return new Response(JSON.stringify(payload), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return nativeFetch(input, init);
    };
  });

  await page.goto(url + '/index.html#practical_training');
  await page.waitForFunction(() => typeof window.showDashboard === 'function' && typeof window.navigateTo === 'function');
  await page.evaluate(() => {
    if (typeof showDashboard === 'function') showDashboard();
    if (typeof navigateTo === 'function') navigateTo('practical_training');
  });
  await page.waitForSelector('#practice-user-message');
  await page.waitForSelector('#dh-widget');
}

async function installDigitalHumanSpy(page) {
  await page.evaluate(() => {
    window.__dhSpeakCalls = [];
    window.digitalHumanSpeak = (text, options) => {
      window.__dhSpeakCalls.push({
        text: String(text || ''),
        scene: options && options.scene || '',
        bubbleKind: options && options.bubbleKind || '',
      });
    };
  });
}

async function setPracticeModule(page) {
  await page.evaluate(() => {
    window.moduleState.practice.module_code = 'objection_handling';
    window.moduleState.practice.scene_code = 'objection_handling';
    if (typeof window.rerenderCurrentPage === 'function') window.rerenderCurrentPage();
    const moduleInput = document.getElementById('practice-module-code');
    if (moduleInput) moduleInput.value = 'objection_handling';
  });
  await page.waitForFunction(() => !!document.getElementById('practice-module-code'));
}

async function sendPracticeMessage(page, text) {
  await page.evaluate((nextText) => {
    const input = document.getElementById('practice-user-message');
    input.value = nextText;
    return window.submitPracticeChat('send');
  }, text);
}

async function run() {
  const server = createStaticServer(ROOT);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  const baseUrl = 'http://127.0.0.1:' + address.port;
  const browser = await chromium.launch({
    headless: true,
    executablePath: resolveBrowserExecutable(),
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });

  try {
    await bootstrapPracticePage(page, baseUrl);
    await installDigitalHumanSpy(page);
    await setPracticeModule(page);

    const migratedPrefs = await page.evaluate(() => window.getDigitalHumanPreferences());
    assert.equal(migratedPrefs.scenes.practice_score, false);
    assert.equal(migratedPrefs.scenes.practice_turn_feedback, true);

    await sendPracticeMessage(page, 'Why is this one so expensive?');
    await page.waitForFunction(() => document.querySelectorAll('.practice-msg-row--ai').length === 1, null, {
      timeout: 5000,
    });

    const firstTurn = await page.evaluate(() => ({
      cardCount: document.querySelectorAll('.practice-turn-feedback-card').length,
      rightPanelText: (document.getElementById('practice-score-panel') || {}).textContent || '',
      toolbarButtons: Array.from(document.querySelectorAll('.practice-toolbar-actions > button')).map((el) => String(el.textContent || '').trim()),
      speechCalls: window.__dhSpeakCalls.slice(),
      pageSpeech: window.digitalHumanGetPageSpeechText('practical_training'),
      assistantReply: String((((window.moduleState || {}).practice || {}).chatResult || {}).assistant_reply || '').trim(),
      latestVoiceAdvice: String(((((window.moduleState || {}).practice || {}).latestTurnFeedback) || {}).voice_advice || '').trim(),
    }));

    assert.equal(firstTurn.cardCount, 0);
    assert.equal(firstTurn.toolbarButtons.length, 1);
    assert.notEqual(firstTurn.rightPanelText.trim(), '');
    assert.equal(firstTurn.speechCalls.length, 1);
    assert.equal(firstTurn.speechCalls[0].scene, 'practice_turn_feedback');
    assert.equal(firstTurn.speechCalls[0].text, firstTurn.latestVoiceAdvice);
    assert.notEqual(firstTurn.speechCalls[0].text, firstTurn.assistantReply);
    assert.equal(firstTurn.pageSpeech, firstTurn.speechCalls[0].text);

    await page.evaluate(() => {
      window.__dhSpeakCalls = [];
      window.setDigitalHumanPreferences({ scenes: { practice_turn_feedback: false } });
    });

    await sendPracticeMessage(page, 'Let me think about it again.');
    await page.waitForFunction(() => document.querySelectorAll('.practice-msg-row--ai').length === 2, null, {
      timeout: 5000,
    });

    const secondTurn = await page.evaluate(() => ({
      cardCount: document.querySelectorAll('.practice-turn-feedback-card').length,
      speechCalls: window.__dhSpeakCalls.slice(),
    }));
    assert.equal(secondTurn.cardCount, 0);
    assert.equal(secondTurn.speechCalls.length, 0);

    await page.evaluate(() => window.submitPracticeResume());
    await page.waitForFunction(() => document.querySelectorAll('.practice-msg-row--ai').length === 2, null, {
      timeout: 5000,
    });

    const resumed = await page.evaluate(() => ({
      cardCount: document.querySelectorAll('.practice-turn-feedback-card').length,
      assistantTurnCount: Array.from(document.querySelectorAll('.practice-msg-row--ai .practice-turn-feedback-card')).length,
    }));
    assert.equal(resumed.cardCount, 0);
    assert.equal(resumed.assistantTurnCount, 0);

    await page.evaluate(() => {
      window.moduleState.practice.evaluationResult = {
        overall_score: 88,
        score_breakdown: { opening: 82, probing: 85, recommendation: 91, closing: 86 },
        strengths: ['浠峰€兼媶瑙ｆ洿娓呮'],
        improvements: ['杩介棶杩樺彲浠ユ洿鑱氱劍'],
        coach_summary: '杩欒疆璇勫垎鎬荤粨',
      };
      window.moduleState.practice.mentorSentence = '杩欐槸璇勫垎鍚庣殑瀵煎笀鐐硅瘎';
      window.moduleState.practice.lastDigitalHumanSpeech = '杩欐槸璇勫垎鍚庣殑瀵煎笀鐐硅瘎';
      if (typeof window.rerenderCurrentPage === 'function') window.rerenderCurrentPage();
      window.__ttsRequests = [];
      window.XMLHttpRequest = class FakeTtsXHR {
        open(method, url) {
          this.method = method;
          this.url = url;
        }
        setRequestHeader() {}
        send(body) {
          const payload = JSON.parse(String(body || '{}'));
          window.__ttsRequests.push({ url: this.url, payload });
          this.status = 200;
          this.response = new Blob(['fake-audio'], { type: 'audio/mpeg' });
          if (typeof this.onload === 'function') this.onload();
        }
      };
      window.Audio = class FakeAudio {
        play() {
          return Promise.resolve();
        }
        pause() {}
      };
      URL.createObjectURL = () => 'blob:practice-replay-audio';
      URL.revokeObjectURL = () => {};
    });
    await page.waitForFunction(() => {
      const panel = document.getElementById('practice-score-panel');
      return panel && String(panel.textContent || '').includes('88');
    }, null, { timeout: 5000 });

    await page.evaluate(() => {
      if (typeof window.digitalHumanExpand === 'function') window.digitalHumanExpand();
    });
    await page.evaluate(() => {
      const target = document.querySelector('.dh-quick-menu-item[data-dh-action="replaySummary"]');
      if (!target) throw new Error('missing replay summary action');
      target.click();
    });
    await page.waitForFunction(() => Array.isArray(window.__ttsRequests) && window.__ttsRequests.length === 1, null, {
      timeout: 5000,
    });

    const replayed = await page.evaluate(() => window.__ttsRequests[0]);
    assert.equal(replayed.payload.text, '杩欐槸璇勫垎鍚庣殑瀵煎笀鐐硅瘎');

    await page.evaluate(async () => {
      await navigateTo('account_settings');
    });
    await page.waitForSelector('#settings-dh-practice-voice');
    await page.waitForSelector('#settings-dh-practice-turn-feedback-voice');

    const settingsRows = await page.evaluate(() => ({
      scoreRow: (document.getElementById('settings-dh-practice-voice') || {}).textContent || '',
      turnRow: (document.getElementById('settings-dh-practice-turn-feedback-voice') || {}).textContent || '',
    }));
    assert.notEqual(settingsRows.scoreRow.trim(), '');
    assert.notEqual(settingsRows.turnRow.trim(), '');

    console.log('practice-turn-feedback.test.js passed');
  } finally {
    await browser.close();
    await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
