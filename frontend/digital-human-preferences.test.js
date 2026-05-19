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

async function bootstrapPage(page, url) {
  await page.goto(url + '/index.html#practical_training');
  await page.evaluate(() => {
    Object.keys(localStorage).forEach((key) => {
      if (String(key || '').indexOf('digital_human_') === 0) localStorage.removeItem(key);
    });
    localStorage.setItem('token', 'dev-token');
    localStorage.setItem('role', 'trainee');
    localStorage.setItem('username', 'trainee');
    localStorage.setItem('displayName', 'Trainee');
    localStorage.setItem('userId', '1');
    localStorage.setItem('store_id', 'store-1');
    localStorage.setItem('store_name', '测试门店');
    if (typeof showDashboard === 'function') showDashboard();
  });
  await page.waitForSelector('#dh-widget');
}

async function installVoiceTestDoubles(page) {
  await page.evaluate(() => {
    window.__dhVoiceTest = { requests: [], toasts: [], fail: false };

    function FakeXHR() {
      this.headers = {};
      this.status = 0;
      this.response = null;
    }

    FakeXHR.prototype.open = function(method, url) {
      this.method = method;
      this.url = url;
    };

    FakeXHR.prototype.setRequestHeader = function(key, value) {
      this.headers[key] = value;
    };

    FakeXHR.prototype.send = function(body) {
      window.__dhVoiceTest.requests.push({
        method: this.method,
        url: this.url,
        body: body,
      });
      this.status = window.__dhVoiceTest.fail ? 500 : 200;
      this.response = window.__dhVoiceTest.fail ? null : new Blob(['ok'], { type: 'audio/mpeg' });
      if (typeof this.onload === 'function') this.onload();
    };

    window.XMLHttpRequest = FakeXHR;
    window.Audio = function AudioStub() {
      this.src = '';
      this.pause = function() {};
      this.play = function() {
        return Promise.resolve();
      };
    };
    window.showToast = function(message, type) {
      window.__dhVoiceTest.toasts.push({
        message: String(message || ''),
        type: String(type || ''),
      });
    };
  });
}

async function assertSettingsAccessAndPersistence(page) {
  await page.evaluate(async () => {
    if (typeof navigateTo === 'function') await navigateTo('account_settings');
  });
  await page.waitForSelector('#settings-dh-auto-voice');
  await page.waitForSelector('#settings-dh-mouse-follow');

  const initial = await page.evaluate(() => ({
    title: (document.querySelector('.settings-page .app-page-title') || {}).textContent || '',
    auto: document.getElementById('settings-dh-auto-voice').getAttribute('aria-pressed'),
    practiceDisabled: document.getElementById('settings-dh-practice-voice').getAttribute('aria-disabled'),
    mouseFollowDisabled: document.getElementById('settings-dh-mouse-follow').getAttribute('aria-disabled'),
  }));

  assert.equal(initial.title, '偏好设置', 'trainee should be able to open account settings');
  assert.equal(initial.auto, 'true', 'auto voice should default to enabled');
  assert.equal(initial.practiceDisabled, 'false', 'scene toggles should be enabled when auto voice is on');
  assert.equal(initial.mouseFollowDisabled, 'false', 'interaction toggles should be enabled while digital human is available');

  await page.click('#settings-dh-auto-voice');

  const afterDisable = await page.evaluate(() => {
    const stored = JSON.parse(localStorage.getItem('digital_human_prefs_v1:1') || '{}');
    return {
      autoPressed: document.getElementById('settings-dh-auto-voice').getAttribute('aria-pressed'),
      practiceDisabled: document.getElementById('settings-dh-practice-voice').getAttribute('aria-disabled'),
      assistantDisabled: document.getElementById('settings-dh-assistant-voice').getAttribute('aria-disabled'),
      stored: stored,
    };
  });

  assert.equal(afterDisable.autoPressed, 'false', 'auto voice row should toggle off');
  assert.equal(afterDisable.practiceDisabled, 'true', 'practice row should be disabled when auto voice is off');
  assert.equal(afterDisable.assistantDisabled, 'true', 'assistant row should be disabled when auto voice is off');
  assert.equal(afterDisable.stored.auto_voice_enabled, false, 'stored auto voice preference should be false');
  assert.equal(afterDisable.stored.scenes.practice_score, true, 'scene preference should be preserved when auto voice is off');

  await page.click('#settings-dh-auto-voice');
  await page.click('#settings-dh-assistant-voice');
  await page.click('#settings-dh-mouse-follow');
  await page.click('#settings-dh-page-greeting');
  await page.evaluate(() => {
    if (typeof rerenderCurrentPage === 'function') rerenderCurrentPage();
  });
  await page.waitForSelector('#settings-dh-assistant-voice');

  const persisted = await page.evaluate(() => {
    const stored = JSON.parse(localStorage.getItem('digital_human_prefs_v1:1') || '{}');
    return {
      assistantPressed: document.getElementById('settings-dh-assistant-voice').getAttribute('aria-pressed'),
      assistantDisabled: document.getElementById('settings-dh-assistant-voice').getAttribute('aria-disabled'),
      mouseFollowPressed: document.getElementById('settings-dh-mouse-follow').getAttribute('aria-pressed'),
      pageGreetingPressed: document.getElementById('settings-dh-page-greeting').getAttribute('aria-pressed'),
      stored: stored,
    };
  });

  assert.equal(persisted.assistantPressed, 'false', 'assistant scene toggle should persist after rerender');
  assert.equal(persisted.assistantDisabled, 'false', 'assistant scene toggle should be enabled again when auto voice is on');
  assert.equal(persisted.mouseFollowPressed, 'false', 'mouse-follow toggle should persist after rerender');
  assert.equal(persisted.pageGreetingPressed, 'false', 'page greeting toggle should persist after rerender');
  assert.equal(persisted.stored.auto_voice_enabled, true, 'stored auto voice preference should be true after re-enable');
  assert.equal(persisted.stored.scenes.assistant, false, 'stored assistant scene preference should persist');
  assert.equal(persisted.stored.mouse_follow, false, 'stored mouse-follow preference should persist');
  assert.equal(persisted.stored.page_greeting, false, 'stored page greeting preference should persist');
}

async function assertAvatarMuteShortcutSync(page) {
  await page.evaluate(async () => {
    window.setDigitalHumanPreferences({
      voice_muted: false,
      auto_voice_enabled: true,
      scenes: {
        practice_score: true,
        practice_turn_feedback: true,
        assistant: true,
        knowledge_qa: true,
      },
    });
    if (typeof navigateTo === 'function') await navigateTo('practical_training');
  });
  await page.waitForSelector('#dh-widget');

  await page.evaluate(() => {
    if (typeof window.digitalHumanExpand === 'function') window.digitalHumanExpand();
  });
  await page.waitForFunction(() => {
    return Array.from(document.querySelectorAll('.dh-quick-menu-item')).some((el) => {
      return String(el.getAttribute('data-dh-action') || '').trim() === 'toggleAutoVoice';
    });
  }, null, { timeout: 5000 });

  const initial = await page.evaluate(() => {
    const shortcut = Array.from(document.querySelectorAll('.dh-quick-menu-item')).find((el) => {
      return String(el.getAttribute('data-dh-action') || '').trim() === 'toggleAutoVoice';
    });
    const badge = document.getElementById('dh-auto-voice-status');
    return {
      shortcutLabel: shortcut ? String(shortcut.textContent || '').trim() : '',
      badgeHidden: badge ? badge.hidden : null,
      shortcutHasMuteIcon: shortcut ? !!shortcut.querySelector('svg') : false,
    };
  });

  assert.equal(initial.shortcutLabel, '开启静音', 'avatar quick menu should offer a global mute switch while voice is active');
  assert.equal(initial.badgeHidden, true, 'mute status badge should stay hidden while auto voice is enabled');
  assert.equal(initial.shortcutHasMuteIcon, true, 'avatar quick menu should render a status icon for mute control');

  await page.evaluate(() => {
    const shortcut = Array.from(document.querySelectorAll('.dh-quick-menu-item')).find((el) => {
      return String(el.getAttribute('data-dh-action') || '').trim() === 'toggleAutoVoice';
    });
    if (!shortcut) throw new Error('missing avatar mute shortcut');
    shortcut.click();
  });

  const afterShortcut = await page.evaluate(() => {
    const stored = JSON.parse(localStorage.getItem('digital_human_prefs_v1:1') || '{}');
    const badge = document.getElementById('dh-auto-voice-status');
    const shortcut = Array.from(document.querySelectorAll('.dh-quick-menu-item')).find((el) => {
      return String(el.getAttribute('data-dh-action') || '').trim() === 'toggleAutoVoice';
    });
    return {
      voiceMuted: stored.voice_muted,
      badgeHidden: badge ? badge.hidden : null,
      badgeLabel: badge ? String(badge.getAttribute('aria-label') || '') : '',
      badgeIconOnly: badge ? badge.childElementCount === 1 : false,
      subtitleText: String((document.getElementById('dh-subtitle-text') || {}).textContent || '').trim(),
      menuOpen: !!(document.getElementById('dh-widget') && document.getElementById('dh-widget').classList.contains('dh-menu-open')),
      shortcutLabel: shortcut ? String(shortcut.textContent || '').trim() : '',
    };
  });

  assert.equal(afterShortcut.voiceMuted, true, 'avatar shortcut should persist the global mute preference');
  assert.equal(afterShortcut.badgeHidden, false, 'mute status badge should show after muting');
  assert.equal(afterShortcut.badgeLabel, '静音模式', 'mute status badge should expose the muted state accessibly');
  assert.equal(afterShortcut.badgeIconOnly, true, 'mute status badge should stay lightweight and icon-only');
  assert.equal(afterShortcut.subtitleText, '已开启静音模式，后续仅显示文字提示', 'avatar shortcut should give immediate mode feedback');
  assert.equal(afterShortcut.menuOpen, false, 'clicking the avatar shortcut should close the quick menu');
  assert.equal(afterShortcut.shortcutLabel, '取消静音', 'avatar shortcut label should flip after entering mute mode');

  await installVoiceTestDoubles(page);
  await page.evaluate(() => {
    window.__dhVoiceTest.requests = [];
    window.digitalHumanSpeak('静音后手动重播测试', {
      bubbleKind: 'score',
      silent: true,
      trigger: 'manual',
      scene: 'score',
    });
    if (typeof window.digitalHumanExpand === 'function') window.digitalHumanExpand();
  });
  await page.waitForFunction(() => {
    return Array.from(document.querySelectorAll('.dh-quick-menu-item')).some((el) => {
      return String(el.getAttribute('data-dh-action') || '').trim() === 'replaySummary';
    });
  }, null, { timeout: 5000 });
  await page.evaluate(() => {
    const replay = Array.from(document.querySelectorAll('.dh-quick-menu-item')).find((el) => {
      return String(el.getAttribute('data-dh-action') || '').trim() === 'replaySummary';
    });
    if (!replay) throw new Error('missing replay action');
    replay.click();
  });

  const mutedReplay = await page.evaluate(() => ({
    requests: window.__dhVoiceTest.requests.length,
    text: String((document.getElementById('dh-subtitle-text') || {}).textContent || '').trim(),
    stopHidden: document.getElementById('dh-stop-speech-btn').hidden,
  }));

  assert.equal(mutedReplay.requests, 0, 'manual replay action should stay silent while global mute is enabled');
  assert.equal(mutedReplay.text, '静音后手动重播测试', 'manual replay action should still surface the bubble text while muted');
  assert.equal(mutedReplay.stopHidden, true, 'manual replay should not expose stop controls while global mute is enabled');

  await page.evaluate(async () => {
    if (typeof navigateTo === 'function') await navigateTo('account_settings');
  });
  await page.waitForSelector('#settings-dh-voice-muted');
  const mutedSettings = await page.evaluate(() => ({
    mutePressed: document.getElementById('settings-dh-voice-muted').getAttribute('aria-pressed'),
  }));
  assert.equal(mutedSettings.mutePressed, 'true', 'account settings should reflect avatar mute shortcut changes');

  await page.click('#settings-dh-voice-muted');

  await page.evaluate(async () => {
    if (typeof navigateTo === 'function') await navigateTo('practical_training');
  });
  await page.waitForSelector('#dh-widget');
  await page.evaluate(() => {
    if (typeof window.digitalHumanExpand === 'function') window.digitalHumanExpand();
  });
  await page.waitForFunction(() => {
    return Array.from(document.querySelectorAll('.dh-quick-menu-item')).some((el) => {
      return String(el.getAttribute('data-dh-action') || '').trim() === 'toggleAutoVoice';
    });
  }, null, { timeout: 5000 });

  const afterSettings = await page.evaluate(() => {
    const shortcut = Array.from(document.querySelectorAll('.dh-quick-menu-item')).find((el) => {
      return String(el.getAttribute('data-dh-action') || '').trim() === 'toggleAutoVoice';
    });
    const badge = document.getElementById('dh-auto-voice-status');
    return {
      shortcutLabel: shortcut ? String(shortcut.textContent || '').trim() : '',
      badgeHidden: badge ? badge.hidden : null,
    };
  });

  assert.equal(afterSettings.shortcutLabel, '开启静音', 'avatar shortcut should switch back to the mute affordance after leaving mute mode');
  assert.equal(afterSettings.badgeHidden, true, 'mute status badge should hide after leaving mute mode');
}

async function assertVoicePreferenceBehavior(page) {
  await page.evaluate(async () => {
    if (typeof navigateTo === 'function') await navigateTo('practical_training');
  });
  await page.waitForSelector('#dh-widget');
  await installVoiceTestDoubles(page);

  await page.evaluate(() => {
    window.__dhVoiceTest.requests = [];
    window.setDigitalHumanPreferences({
      voice_muted: false,
      auto_voice_enabled: false,
      scenes: {
        practice_score: true,
        assistant: true,
        knowledge_qa: true,
      },
    });
    window.digitalHumanSpeak('自动播报静音测试', {
      bubbleKind: 'score',
      trigger: 'auto',
      scene: 'practice',
    });
  });

  const autoMuted = await page.evaluate(() => ({
    requests: window.__dhVoiceTest.requests.length,
    text: document.getElementById('dh-subtitle-text').textContent,
    stopHidden: document.getElementById('dh-stop-speech-btn').hidden,
  }));

  assert.equal(autoMuted.requests, 0, 'auto speech should not request TTS when global auto voice is off');
  assert.equal(autoMuted.text, '自动播报静音测试', 'auto muted speech should still keep the bubble text');
  assert.equal(autoMuted.stopHidden, true, 'stop button should stay hidden when no audio is playing');

  await page.evaluate(() => {
    window.__dhVoiceTest.requests = [];
    window.setDigitalHumanPreferences({
      voice_muted: false,
      auto_voice_enabled: true,
      scenes: {
        practice_score: false,
        assistant: true,
        knowledge_qa: true,
      },
    });
    window.digitalHumanSpeak('陪练自动关闭', {
      bubbleKind: 'score',
      trigger: 'auto',
      scene: 'practice',
    });
    window.digitalHumanSpeak('助手自动开启', {
      bubbleKind: 'assistant',
      trigger: 'auto',
      scene: 'assistant',
    });
  });

  const sceneScoped = await page.evaluate(() => ({
    requests: window.__dhVoiceTest.requests.length,
    text: document.getElementById('dh-subtitle-text').textContent,
    stopHidden: document.getElementById('dh-stop-speech-btn').hidden,
  }));

  assert.equal(sceneScoped.requests, 1, 'only enabled auto voice scenes should request TTS');
  assert.equal(sceneScoped.text, '助手自动开启', 'latest enabled auto speech should update the bubble text');
  assert.equal(sceneScoped.stopHidden, false, 'stop button should show while manual or enabled auto audio is playing');

  await page.evaluate(() => {
    window.__dhVoiceTest.requests = [];
    window.setDigitalHumanPreferences({
      voice_muted: false,
      auto_voice_enabled: false,
      scenes: {
        practice_score: false,
        assistant: false,
        knowledge_qa: false,
      },
    });
    window.digitalHumanSpeak('手动播报测试', {
      bubbleKind: 'score',
      trigger: 'manual',
      scene: 'score',
    });
  });

  const manualSpeech = await page.evaluate(() => ({
    requests: window.__dhVoiceTest.requests.length,
    text: document.getElementById('dh-subtitle-text').textContent,
    stopHidden: document.getElementById('dh-stop-speech-btn').hidden,
  }));

  assert.equal(manualSpeech.requests, 1, 'manual speech should bypass auto voice preferences');
  assert.equal(manualSpeech.text, '手动播报测试', 'manual speech should update the bubble text');
  assert.equal(manualSpeech.stopHidden, false, 'manual speech should expose the stop button');

  await page.evaluate(() => {
    window.__dhVoiceTest.requests = [];
    window.setDigitalHumanPreferences({
      voice_muted: true,
      auto_voice_enabled: true,
      scenes: {
        practice_score: true,
        assistant: true,
        knowledge_qa: true,
      },
    });
    window.digitalHumanSpeak('全局静音测试', {
      bubbleKind: 'score',
      trigger: 'manual',
      scene: 'score',
    });
  });

  const globallyMuted = await page.evaluate(() => ({
    requests: window.__dhVoiceTest.requests.length,
    text: document.getElementById('dh-subtitle-text').textContent,
    stopHidden: document.getElementById('dh-stop-speech-btn').hidden,
  }));

  assert.equal(globallyMuted.requests, 0, 'global mute should silence manual playback as well');
  assert.equal(globallyMuted.text, '全局静音测试', 'global mute should still keep the bubble text');
  assert.equal(globallyMuted.stopHidden, true, 'global mute should keep playback controls hidden');

  await page.evaluate(() => {
    window.digitalHumanStopSpeech({ preserveBubble: true });
  });

  const afterStop = await page.evaluate(() => ({
    text: document.getElementById('dh-subtitle-text').textContent,
    stopHidden: document.getElementById('dh-stop-speech-btn').hidden,
  }));

  assert.equal(afterStop.text, '全局静音测试', 'stopping speech should preserve the current bubble text');
  assert.equal(afterStop.stopHidden, true, 'stop button should hide after stopping playback');

  await page.evaluate(() => {
    window.__dhVoiceTest.requests = [];
    window.__dhVoiceTest.toasts = [];
    window.__dhVoiceTest.fail = true;
    window.setDigitalHumanPreferences({ voice_muted: false });
    window.digitalHumanSpeak('失败后保留文本', {
      bubbleKind: 'score',
      trigger: 'manual',
      scene: 'score',
    });
  });
  await page.waitForTimeout(30);

  const failure = await page.evaluate(() => ({
    requests: window.__dhVoiceTest.requests.length,
    text: document.getElementById('dh-subtitle-text').textContent,
    toastCount: window.__dhVoiceTest.toasts.length,
    lastToast: window.__dhVoiceTest.toasts.length ? window.__dhVoiceTest.toasts[window.__dhVoiceTest.toasts.length - 1].message : '',
    stopHidden: document.getElementById('dh-stop-speech-btn').hidden,
  }));

  assert.equal(failure.requests, 1, 'failed speech should still attempt a single TTS request');
  assert.equal(failure.text, '失败后保留文本', 'failed speech should keep the original bubble text');
  assert.ok(failure.toastCount >= 1, 'failed speech should show an error toast');
  assert.notEqual(failure.lastToast, '', 'failed speech toast should contain a message');
  assert.equal(failure.stopHidden, true, 'stop button should hide after a failed speech attempt');
}

async function assertHomeVoicePreferenceSyncAfterLogin(browser, baseUrl) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  try {
    await page.addInitScript(() => {
      localStorage.setItem('digital_human_prefs_v1:42', JSON.stringify({
        voice_muted: false,
        auto_voice_enabled: true,
        scenes: {
          practice_score: true,
          practice_turn_feedback: true,
          assistant: true,
          knowledge_qa: true,
        },
        minimax_voice_id: 'female-yujie',
      }));
    });

    await page.goto(baseUrl + '/index.html');
    await page.evaluate(() => {
      localStorage.setItem('token', 'dev-token');
      localStorage.setItem('role', 'trainee');
      localStorage.setItem('username', 'trainee42');
      localStorage.setItem('displayName', 'Trainee 42');
      localStorage.setItem('userId', '42');
      localStorage.setItem('store_id', 'store-1');
      localStorage.setItem('store_name', '娴嬭瘯闂ㄥ簵');
      if (typeof showDashboard === 'function') showDashboard();
    });
    await page.waitForSelector('#dh-widget');
    await installVoiceTestDoubles(page);

    await page.evaluate(() => {
      window.__dhVoiceTest.requests = [];
      window.digitalHumanSpeak('棣栭〉闊宠壊鍚屾娴嬭瘯', {
        bubbleKind: 'daily',
        trigger: 'manual',
        scene: 'daily',
      });
    });

    await page.waitForFunction(() => window.__dhVoiceTest.requests.length === 1);
    const payload = await page.evaluate(() => JSON.parse(window.__dhVoiceTest.requests[0].body || '{}'));
    assert.equal(payload.voice_id, 'female-yujie', 'home digital human should reload the logged-in user voice preference');
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
    await bootstrapPage(page, baseUrl);
    await assertSettingsAccessAndPersistence(page);
    await assertAvatarMuteShortcutSync(page);
    await assertVoicePreferenceBehavior(page);
    await assertHomeVoicePreferenceSyncAfterLogin(browser, baseUrl);
    console.log('digital-human-preferences.test.js passed');
  } finally {
    await browser.close();
    await new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  }
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
