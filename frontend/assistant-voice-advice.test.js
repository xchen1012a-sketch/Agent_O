const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('playwright');

const ROOT = __dirname;
const BROWSER_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
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

async function bootstrapAssistantPage(page, url) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'dev-token');
    localStorage.setItem('role', 'admin');
    localStorage.setItem('username', 'admin');
    localStorage.setItem('displayName', 'Admin');
    localStorage.setItem('userId', '1');
    localStorage.setItem('store_id', 'store-1');
    localStorage.setItem('store_name', '测试门店');
    window.__assistantReplyMock = {
      reply_script: '完整回复话术：这款主要贵在材质、工艺和售后保障。',
      followup_question: '顾客更在意预算范围还是佩戴场景？',
      coach_tip: '先认同预算顾虑，再拆价值和使用场景，别急着报优惠。',
      voice_advice: '先认同预算顾虑，再拆价值和使用场景，别急着报优惠。',
    };
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
              store_name: '测试门店',
            },
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }
        );
      }
      if (url.includes('/api/assistant/reply')) {
        return new Response(
          JSON.stringify({
            code: 200,
            message: 'success',
            data: window.__assistantReplyMock,
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }
        );
      }
      if (url.includes('/api/system-settings/digital-human')) {
        return new Response(
          JSON.stringify({
            code: 200,
            message: 'success',
            data: window.__digitalHumanSystemSettings,
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }
        );
      }
      return nativeFetch(input, init);
    };
  });
  await page.goto(url + '/index.html#on_duty_assistant');
  await page.waitForFunction(() => {
    return typeof window.navigateTo === 'function' && typeof window.showDashboard === 'function';
  }, null, { timeout: 30000 });
  await page.evaluate(() => {
    if (typeof showDashboard === 'function') showDashboard();
    if (typeof navigateTo === 'function') navigateTo('on_duty_assistant');
  });

  await page.waitForSelector('#assistant-scene-input');
  await page.waitForSelector('#dh-widget');
}

async function installAssistantReplyMock(page) {
  await page.evaluate(() => {
    window.__realDigitalHumanSpeak = window.digitalHumanSpeak;
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
    await bootstrapAssistantPage(page, baseUrl);
    await installAssistantReplyMock(page);

    await page.evaluate(() => {
      const input = document.getElementById('assistant-scene-input');
      input.value = '顾客说这款太贵了';
      if (typeof window.assistantResizeSceneInput === 'function') {
        window.assistantResizeSceneInput();
      }
      return window.submitAssistantReply();
    });

    await page.waitForFunction(() => {
      return Array.isArray(window.__dhSpeakCalls) && window.__dhSpeakCalls.length === 1;
    }, null, { timeout: 5000 });

    const autoSpeak = await page.evaluate(() => ({
      calls: window.__dhSpeakCalls.slice(),
      speechText: window.digitalHumanGetPageSpeechText('on_duty_assistant'),
      bubbles: Array.from(document.querySelectorAll('.assistant-ai-bubble')).map((el) => String(el.textContent || '').trim()),
    }));

    assert.equal(autoSpeak.calls[0].text, '先认同预算顾虑，再拆价值和使用场景，别急着报优惠。');
    assert.equal(autoSpeak.speechText, '先认同预算顾虑，再拆价值和使用场景，别急着报优惠。');
    assert.ok(autoSpeak.bubbles.some((text) => text.includes('完整回复话术：这款主要贵在材质、工艺和售后保障。')));
    assert.ok(autoSpeak.bubbles.some((text) => text.includes('顾客更在意预算范围还是佩戴场景？')));
    assert.ok(!autoSpeak.calls[0].text.includes('完整回复话术'));

    await page.evaluate((expectedSpeech) => {
      const assistant = window.moduleState && window.moduleState.assistant;
      if (assistant && assistant.replyResult) {
        assistant.lastDigitalHumanSpeech = '';
        assistant.latestTurnFeedback = {
          voice_advice: expectedSpeech,
        };
        assistant.replyResult = {
          ...assistant.replyResult,
          voice_advice: '',
          coach_tip: '',
          turn_feedback: {
            voice_advice: expectedSpeech,
          },
        };
      }
      window.__ttsRequests = [];
      Object.defineProperty(window, 'MediaSource', {
        configurable: true,
        value: undefined,
      });
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
      URL.createObjectURL = () => 'blob:fake-audio';
      URL.revokeObjectURL = () => {};
    }, autoSpeak.calls[0].text);

    const replaySpeechText = await page.evaluate(() => window.digitalHumanGetPageSpeechText('on_duty_assistant'));
    assert.equal(replaySpeechText, autoSpeak.calls[0].text);

    await page.evaluate(() => {
      if (typeof window.digitalHumanExpand === 'function') window.digitalHumanExpand();
    });
    await page.evaluate(() => {
      const items = Array.from(document.querySelectorAll('.dh-quick-menu-item'));
      const target = items.find((el) => {
        const title = String(el.getAttribute('title') || '').trim();
        const text = String(el.textContent || '').trim();
        return title === '读出建议' || text.includes('读出建议');
      });
      if (!target) throw new Error('missing read advice action');
      target.click();
    });
    await page.waitForFunction(() => Array.isArray(window.__ttsRequests) && window.__ttsRequests.length === 1, null, {
      timeout: 5000,
    });

    const manualSpeak = await page.evaluate(() => window.__ttsRequests[0]);
    assert.equal(manualSpeak.payload.text, '先认同预算顾虑，再拆价值和使用场景，别急着报优惠。');

    await page.evaluate(() => {
      window.__digitalHumanSystemSettings = {
        enabled: true,
        tts_provider: 'browser',
        minimax_configured: true,
      };
      window.__ttsRequests = [];
      window.__speechCalls = [];
      Object.defineProperty(window, 'SpeechSynthesisUtterance', {
        configurable: true,
        writable: true,
        value: function FakeSpeechSynthesisUtterance(text) {
          this.text = String(text || '');
          this.lang = '';
          this.onend = null;
          this.onerror = null;
        },
      });
      Object.defineProperty(window, 'speechSynthesis', {
        configurable: true,
        value: {
          speak(utterance) {
            window.__speechCalls.push({ text: utterance && utterance.text || '', lang: utterance && utterance.lang || '' });
            if (utterance && typeof utterance.onend === 'function') utterance.onend();
          },
          cancel() {},
        },
      });
      window.digitalHumanSpeak = window.__realDigitalHumanSpeak;
      window.getDigitalHumanTtsProvider = () => 'browser';
      window.isDigitalHumanSystemEnabled = () => true;
    });
    await page.evaluate((text) => {
      window.digitalHumanSpeak(text, {
        bubbleKind: 'assistant',
        trigger: 'manual',
        scene: 'assistant',
      });
    }, manualSpeak.payload.text);
    await page.waitForFunction(() => Array.isArray(window.__speechCalls) && window.__speechCalls.length === 1, null, {
      timeout: 5000,
    });

    const browserSpeak = await page.evaluate(() => ({
      speechCalls: window.__speechCalls.slice(),
      ttsRequests: window.__ttsRequests.length,
    }));
    assert.equal(browserSpeak.speechCalls[0].text, manualSpeak.payload.text);
    assert.equal(browserSpeak.ttsRequests, 0);

    console.log('assistant-voice-advice.test.js passed');
  } finally {
    await browser.close();
    await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

function resolveBrowserExecutable() {
  for (let i = 0; i < BROWSER_CANDIDATES.length; i += 1) {
    if (fs.existsSync(BROWSER_CANDIDATES[i])) return BROWSER_CANDIDATES[i];
  }
  return chromium.executablePath();
}
