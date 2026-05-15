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

function resolveBrowserExecutable() {
  for (let i = 0; i < BROWSER_CANDIDATES.length; i += 1) {
    if (fs.existsSync(BROWSER_CANDIDATES[i])) return BROWSER_CANDIDATES[i];
  }
  return chromium.executablePath();
}

async function bootstrapAdminPage(page, url) {
  await page.addInitScript(() => {
    localStorage.setItem('token', 'dev-token');
    localStorage.setItem('role', 'admin');
    localStorage.setItem('username', 'admin');
    localStorage.setItem('displayName', 'Admin');
    localStorage.setItem('userId', '1');
    localStorage.setItem('store_id', 'store-1');
    localStorage.setItem('store_name', '娴嬭瘯闂ㄥ簵');

    window.__systemSettingsState = {
      enabled: true,
      tts_provider: 'minimax',
      minimax_configured: true,
    };
    window.__systemSettingsPatches = [];
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
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }
        );
      }
      if (url.includes('/api/system-settings/digital-human')) {
        const method = String((init && init.method) || 'GET').toUpperCase();
        if (method === 'PATCH') {
          const patch = JSON.parse(String(init && init.body || '{}'));
          window.__systemSettingsState = Object.assign({}, window.__systemSettingsState, patch);
          window.__systemSettingsPatches.push(patch);
        }
        return new Response(
          JSON.stringify({
            code: 200,
            message: 'success',
            data: window.__systemSettingsState,
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

  await page.goto(url + '/index.html#system_settings');
  await page.waitForFunction(() => typeof window.showDashboard === 'function' && typeof window.navigateTo === 'function');
  await page.evaluate(() => {
    if (typeof showDashboard === 'function') showDashboard();
    if (typeof navigateTo === 'function') navigateTo('system_settings');
  });
  await page.waitForSelector('#system-dh-enabled');
}

async function assertAdminSystemSettingsPage(page) {
  const initial = await page.evaluate(() => ({
    title: (document.querySelector('#content-area .app-page-title') || {}).textContent || '',
    enabledPressed: document.getElementById('system-dh-enabled').getAttribute('aria-pressed'),
    minimaxPressed: document.getElementById('system-dh-provider-minimax').getAttribute('aria-pressed'),
    browserPressed: document.getElementById('system-dh-provider-browser').getAttribute('aria-pressed'),
  }));

  assert.match(initial.title, /系统设置|数字人管理|绯荤粺璁剧疆|缁崵绮虹拋鍓х枂/);
  assert.equal(initial.enabledPressed, 'true');
  assert.equal(initial.minimaxPressed, 'true');
  assert.equal(initial.browserPressed, 'false');

  await page.click('#system-dh-enabled');
  await page.click('#system-settings-save-btn');
  await page.waitForFunction(() => Array.isArray(window.__systemSettingsPatches) && window.__systemSettingsPatches.length >= 1);

  const afterDisable = await page.evaluate(() => ({
    state: window.__systemSettingsState,
    providerDisabled: document.getElementById('system-dh-provider-group').getAttribute('aria-disabled'),
  }));
  assert.equal(afterDisable.state.enabled, false);
  assert.equal(afterDisable.providerDisabled, 'true');
}

async function assertDigitalHumanManagementInteractionSettings(page) {
  await page.evaluate(async () => {
    await navigateTo('digital_human_settings');
  });
  await page.waitForSelector('#system-dh-mouse-follow');

  const initial = await page.evaluate(() => ({
    title: (document.querySelector('#content-area .app-page-title') || {}).textContent || '',
    mouseFollowPressed: document.getElementById('system-dh-mouse-follow').getAttribute('aria-pressed'),
    dragRotatePressed: document.getElementById('system-dh-drag-rotate').getAttribute('aria-pressed'),
    pageGreetingPressed: document.getElementById('system-dh-page-greeting').getAttribute('aria-pressed'),
  }));

  assert.notEqual(initial.title, '', 'digital human management page should render for admin');
  assert.equal(initial.mouseFollowPressed, 'true');
  assert.equal(initial.dragRotatePressed, 'true');
  assert.equal(initial.pageGreetingPressed, 'true');

  await page.click('#system-dh-mouse-follow');
  await page.click('#system-dh-drag-rotate');
  await page.click('#system-dh-page-greeting');
  await page.evaluate(() => {
    if (typeof rerenderCurrentPage === 'function') rerenderCurrentPage();
  });
  await page.waitForSelector('#system-dh-page-greeting');

  const persisted = await page.evaluate(() => {
    const stored = JSON.parse(localStorage.getItem('digital_human_prefs_v1:1') || '{}');
    return {
      mouseFollowPressed: document.getElementById('system-dh-mouse-follow').getAttribute('aria-pressed'),
      dragRotatePressed: document.getElementById('system-dh-drag-rotate').getAttribute('aria-pressed'),
      pageGreetingPressed: document.getElementById('system-dh-page-greeting').getAttribute('aria-pressed'),
      stored: stored,
    };
  });

  assert.equal(persisted.mouseFollowPressed, 'false');
  assert.equal(persisted.dragRotatePressed, 'false');
  assert.equal(persisted.pageGreetingPressed, 'false');
  assert.equal(persisted.stored.mouse_follow, false);
  assert.equal(persisted.stored.drag_rotate, false);
  assert.equal(persisted.stored.page_greeting, false);

  await page.evaluate(async () => {
    await navigateTo('system_settings');
  });
  await page.waitForSelector('#system-dh-enabled');
}

async function assertProviderSelectionHasVisibleFeedback(page) {
  await page.evaluate(async () => {
    await navigateTo('system_settings');
  });
  await page.waitForSelector('#system-dh-provider-browser');

  const before = await page.evaluate(() => {
    const minimax = document.getElementById('system-dh-provider-minimax');
    const browser = document.getElementById('system-dh-provider-browser');
    const minimaxStyle = window.getComputedStyle(minimax);
    const browserStyle = window.getComputedStyle(browser);
    return {
      minimaxPressed: minimax.getAttribute('aria-pressed'),
      browserPressed: browser.getAttribute('aria-pressed'),
      minimaxBg: minimaxStyle.backgroundColor,
      browserBg: browserStyle.backgroundColor,
      minimaxBorder: minimaxStyle.borderColor,
      browserBorder: browserStyle.borderColor,
    };
  });

  await page.click('#system-dh-provider-browser');

  const after = await page.evaluate(() => {
    const minimax = document.getElementById('system-dh-provider-minimax');
    const browser = document.getElementById('system-dh-provider-browser');
    const minimaxStyle = window.getComputedStyle(minimax);
    const browserStyle = window.getComputedStyle(browser);
    return {
      minimaxPressed: minimax.getAttribute('aria-pressed'),
      browserPressed: browser.getAttribute('aria-pressed'),
      minimaxBg: minimaxStyle.backgroundColor,
      browserBg: browserStyle.backgroundColor,
      minimaxBorder: minimaxStyle.borderColor,
      browserBorder: browserStyle.borderColor,
    };
  });

  assert.equal(before.minimaxPressed, 'true');
  assert.equal(before.browserPressed, 'false');
  assert.notEqual(before.minimaxBg, before.browserBg, 'selected provider should have a distinct background before switching');
  assert.notEqual(before.minimaxBorder, before.browserBorder, 'selected provider should have a distinct border before switching');

  assert.equal(after.minimaxPressed, 'false');
  assert.equal(after.browserPressed, 'true');
  assert.notEqual(after.minimaxBg, after.browserBg, 'selected provider should keep a distinct background after switching');
  assert.notEqual(after.minimaxBorder, after.browserBorder, 'selected provider should keep a distinct border after switching');

  await page.click('#system-dh-provider-minimax');
  await page.waitForFunction(() => {
    var minimax = document.getElementById('system-dh-provider-minimax');
    var browser = document.getElementById('system-dh-provider-browser');
    return minimax && browser
      && minimax.getAttribute('aria-pressed') === 'true'
      && browser.getAttribute('aria-pressed') === 'false';
  });
}

async function assertSystemSettingsDoesNotShowDigitalHuman(page) {
  await page.waitForTimeout(50);
  const state = await page.evaluate(() => ({
    widgetCount: document.querySelectorAll('#dh-widget').length,
    loginModeCount: document.querySelectorAll('.dh-floating-root.dh-login-mode').length,
    pageTitle: (document.querySelector('#content-area .app-page-title') || {}).textContent || '',
  }));

  assert.match(state.pageTitle, /系统设置|数字人管理|绯荤粺璁剧疆|缁崵绮虹拋鍓х枂/);
  assert.equal(state.widgetCount, 0, 'system settings should not keep any digital human widget mounted');
  assert.equal(state.loginModeCount, 0, 'refreshing into system settings should not leave login-mode digital human behind');
}

async function assertGlobalDisableHidesDigitalHuman(page) {
  await page.evaluate(async () => {
    await navigateTo('home');
  });
  await page.waitForTimeout(50);
  let widgetCount = await page.locator('#dh-widget').count();
  assert.equal(widgetCount, 0);

  await page.evaluate(async () => {
    await navigateTo('practical_training');
  });
  await page.waitForTimeout(50);
  widgetCount = await page.locator('#dh-widget').count();
  assert.equal(widgetCount, 0);

  await page.evaluate(async () => {
    await navigateTo('account_settings');
  });
  await page.waitForSelector('#settings-dh-system-note');
  const accountState = await page.evaluate(() => ({
    note: document.getElementById('settings-dh-system-note').textContent,
    autoDisabled: document.getElementById('settings-dh-auto-voice').getAttribute('aria-disabled'),
    practiceDisabled: document.getElementById('settings-dh-practice-voice').getAttribute('aria-disabled'),
  }));
  assert.match(accountState.note, /系统管理员已关闭数字人|绠＄悊鍛樺凡鍏抽棴鏁板瓧浜?/);
  assert.equal(accountState.autoDisabled, 'true');
  assert.equal(accountState.practiceDisabled, 'true');
}

async function assertBrowserProviderUsesSpeechSynthesis(page) {
  await page.evaluate(async () => {
    await navigateTo('system_settings');
  });
  await page.waitForSelector('#system-dh-provider-browser');
  await page.click('#system-dh-enabled');
  await page.waitForFunction(() => {
    var group = document.getElementById('system-dh-provider-group');
    var browserBtn = document.getElementById('system-dh-provider-browser');
    return group && browserBtn
      && group.getAttribute('aria-disabled') === 'false'
      && !browserBtn.disabled;
  });
  await page.click('#system-dh-provider-browser');
  await page.click('#system-settings-save-btn');
  await page.waitForFunction(() => {
    return window.__systemSettingsState && window.__systemSettingsState.enabled === true
      && window.__systemSettingsState.tts_provider === 'browser';
  });

  await page.evaluate(() => {
    window.__ttsRequests = [];
    window.__speechCalls = [];
    window.XMLHttpRequest = class FakeXHR {
      open(method, url) {
        this.method = method;
        this.url = url;
      }
      setRequestHeader() {}
      send(body) {
        window.__ttsRequests.push({ method: this.method, url: this.url, body: body });
        this.status = 200;
        this.response = new Blob(['fake-audio'], { type: 'audio/mpeg' });
        if (typeof this.onload === 'function') this.onload();
      }
    };
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
  });

  await page.evaluate(async () => {
    await navigateTo('on_duty_assistant');
  });
  await page.waitForSelector('#dh-widget');
  await page.evaluate(() => {
    window.digitalHumanSpeak('browser provider speech test', {
      bubbleKind: 'assistant',
      trigger: 'manual',
      scene: 'assistant',
    });
  });
  await page.waitForFunction(() => Array.isArray(window.__speechCalls) && window.__speechCalls.length === 1);

  const speechResult = await page.evaluate(() => ({
    speechCalls: window.__speechCalls.slice(),
    ttsRequests: window.__ttsRequests.length,
  }));
  assert.equal(speechResult.speechCalls[0].text, 'browser provider speech test');
  assert.equal(speechResult.ttsRequests, 0);
}

async function assertTraineeCannotAccessSystemSettings(browser, baseUrl) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  try {
    await page.addInitScript(() => {
      localStorage.setItem('token', 'dev-token');
      localStorage.setItem('role', 'trainee');
      localStorage.setItem('username', 'trainee');
      localStorage.setItem('displayName', 'Trainee');
      localStorage.setItem('userId', '2');
      localStorage.setItem('store_id', 'store-1');
      localStorage.setItem('store_name', '娴嬭瘯闂ㄥ簵');
      const nativeFetch = window.fetch.bind(window);
      window.fetch = async (input, init) => {
        const url = typeof input === 'string' ? input : String(input && input.url || '');
        if (url.includes('/api/me')) {
          return new Response(
            JSON.stringify({
              code: 200,
              message: 'success',
              data: {
                user_id: '2',
                username: 'trainee',
                display_name: 'Trainee',
                role: 'trainee',
                store_id: 'store-1',
                store_name: '娴嬭瘯闂ㄥ簵',
              },
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
              data: {
                enabled: true,
                tts_provider: 'browser',
                minimax_configured: false,
              },
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

    await page.goto(baseUrl + '/index.html#system_settings');
    await page.waitForFunction(() => typeof window.showDashboard === 'function' && typeof window.navigateTo === 'function');
    await page.evaluate(() => {
      if (typeof showDashboard === 'function') showDashboard();
      if (typeof navigateTo === 'function') navigateTo('system_settings');
    });
    await page.waitForTimeout(50);
    const title = await page.evaluate(() => (document.querySelector('#content-area .app-page-title') || {}).textContent || '');
    assert.doesNotMatch(title, /系统设置|数字人管理|绯荤粺璁剧疆|缁崵绮虹拋鍓х枂/);
  } finally {
    await page.close();
  }
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
    await bootstrapAdminPage(page, baseUrl);
    await assertSystemSettingsDoesNotShowDigitalHuman(page);
    await assertProviderSelectionHasVisibleFeedback(page);
    await assertDigitalHumanManagementInteractionSettings(page);
    await assertAdminSystemSettingsPage(page);
    await assertGlobalDisableHidesDigitalHuman(page);
    await assertBrowserProviderUsesSpeechSynthesis(page);
    await page.reload({ waitUntil: 'load' });
    await page.waitForFunction(() => typeof window.showDashboard === 'function' && typeof window.navigateTo === 'function');
    await page.evaluate(() => {
      if (typeof showDashboard === 'function') showDashboard();
      if (typeof navigateTo === 'function') navigateTo('system_settings');
    });
    await page.waitForSelector('#system-dh-enabled');
    await assertSystemSettingsDoesNotShowDigitalHuman(page);
    await assertTraineeCannotAccessSystemSettings(browser, baseUrl);
    console.log('system-settings-digital-human.test.js passed');
  } finally {
    await browser.close();
    await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
