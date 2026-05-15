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

async function bootstrapPage(page, url) {
  await page.goto(url + '/index.html#practical_training');
  await page.evaluate(() => {
    localStorage.setItem('token', 'dev-token');
    localStorage.setItem('role', 'admin');
    localStorage.setItem('username', 'admin');
    localStorage.setItem('displayName', 'Admin');
    localStorage.setItem('userId', '1');
    localStorage.setItem('store_id', 'store-1');
    localStorage.setItem('store_name', '娴嬭瘯闂ㄥ簵');
    if (typeof showDashboard === 'function') showDashboard();
  });
  await page.waitForSelector('#dh-floating-root');
  await page.waitForSelector('#dh-widget');
  await page.waitForSelector('#dh-canvas-wrap canvas');
}

async function clickAvatarUntilPoseChanges(page, fromPose) {
  const changed = await page.evaluate(async (initialPose) => {
    const widget = document.getElementById('dh-widget');
    if (!widget || typeof window.digitalHumanCyclePose !== 'function') return false;
    for (let i = 0; i < 4; i += 1) {
      window.digitalHumanCyclePose();
      await new Promise((resolve) => setTimeout(resolve, 80));
      if ((widget.dataset.pose || '') !== initialPose) return true;
    }
    return false;
  }, fromPose);
  assert.equal(changed, true, 'cycle pose action should change pose');
}

async function assertAvatarViewportFit(page, label) {
  const layout = await page.evaluate(() => {
    const widget = document.getElementById('dh-widget');
    const canvasWrap = document.getElementById('dh-canvas-wrap');
    if (!widget || !canvasWrap) return null;

    function visibleRatio(rect) {
      const visibleWidth = Math.max(0, Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0));
      const visibleHeight = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
      return {
        width: rect.width ? visibleWidth / rect.width : 0,
        height: rect.height ? visibleHeight / rect.height : 0,
      };
    }

    const widgetRect = widget.getBoundingClientRect();
    const canvasRect = canvasWrap.getBoundingClientRect();
    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      widgetRect: { top: widgetRect.top, right: widgetRect.right, bottom: widgetRect.bottom, left: widgetRect.left, width: widgetRect.width, height: widgetRect.height },
      canvasRect: { top: canvasRect.top, right: canvasRect.right, bottom: canvasRect.bottom, left: canvasRect.left, width: canvasRect.width, height: canvasRect.height },
      widgetVisible: visibleRatio(widgetRect),
      canvasVisible: visibleRatio(canvasRect),
    };
  });

  assert.ok(layout, label + ': layout metrics should exist');
  assert.ok(layout.widgetVisible.width >= 0.98, label + ': widget should stay inside viewport width');
  assert.ok(layout.widgetVisible.height >= 0.98, label + ': widget should stay inside viewport height');
  assert.ok(layout.canvasVisible.width >= 0.95, label + ': avatar canvas should stay inside viewport width');
  assert.ok(layout.canvasVisible.height >= 0.92, label + ': avatar canvas should be almost fully visible vertically');
  assert.ok(layout.canvasRect.bottom <= layout.viewport.height + 8, label + ': avatar canvas should stay near the viewport bottom edge');
}

async function assertSpeechBubbleScrollable(page) {
  await page.evaluate(() => {
    const longText = Array.from(
      { length: 140 },
      (_, index) => '数字人提示内容第' + (index + 1) + '条，请继续向下滚动查看完整播报。'
    ).join(' ');
    if (typeof window.digitalHumanSpeak === 'function') {
      window.digitalHumanSpeak(longText, { bubbleKind: 'score', silent: true });
    }
  });

  await page.waitForFunction(() => {
    const bubble = document.getElementById('dh-subtitle');
    const text = document.getElementById('dh-subtitle-text');
    return !!bubble && !!text && bubble.classList.contains('dh-speech-bubble--visible') && text.scrollHeight > text.clientHeight;
  }, null, { timeout: 5000 });

  const initialMetrics = await page.evaluate(() => {
    const bubble = document.getElementById('dh-subtitle');
    const text = document.getElementById('dh-subtitle-text');
    if (!bubble || !text) return null;
    const rect = text.getBoundingClientRect();
    const hitTarget = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return {
      bubblePointerEvents: window.getComputedStyle(bubble).pointerEvents,
      textPointerEvents: window.getComputedStyle(text).pointerEvents,
      hitTargetId: hitTarget ? hitTarget.id : '',
      scrollTop: text.scrollTop,
      scrollHeight: text.scrollHeight,
      clientHeight: text.clientHeight,
    };
  });

  assert.ok(initialMetrics, 'speech bubble metrics should exist');
  assert.equal(initialMetrics.bubblePointerEvents, 'auto', 'speech bubble should accept pointer events for scrolling');
  assert.equal(initialMetrics.textPointerEvents, 'auto', 'speech bubble text should accept pointer events');
  assert.equal(initialMetrics.hitTargetId, 'dh-subtitle-text', 'speech bubble text should win pointer hit-testing');
  assert.ok(initialMetrics.scrollHeight > initialMetrics.clientHeight, 'speech bubble text should overflow before scrolling');

  const scrolledTop = await page.evaluate(() => {
    const text = document.getElementById('dh-subtitle-text');
    if (!text) return -1;
    text.scrollTop = 120;
    return text.scrollTop;
  });

  assert.ok(scrolledTop > initialMetrics.scrollTop, 'speech bubble text should be scrollable once pointer events are enabled');
}

async function assertInteractionPreferencesAffectRuntime(page) {
  await page.evaluate(() => {
    window.setDigitalHumanPreferences({
      mouse_follow: true,
      drag_rotate: true,
    });
  });

  await page.mouse.move(1520, 120);
  await page.waitForTimeout(80);

  const enabledMouseFollow = await page.evaluate(() => window.__getDigitalHumanDebugState());
  assert.equal(enabledMouseFollow.mouseFollowEnabled, true, 'mouse follow should be enabled by default');
  assert.ok(Math.abs(enabledMouseFollow.targetHeadYaw) > 0.05, 'mouse movement should update digital human head target when mouse follow is enabled');

  await page.evaluate(() => {
    window.setDigitalHumanPreferences({
      mouse_follow: false,
      drag_rotate: false,
    });
  });

  await page.mouse.move(120, 120);
  await page.waitForTimeout(80);

  const disabledMouseFollow = await page.evaluate(() => window.__getDigitalHumanDebugState());
  assert.equal(disabledMouseFollow.mouseFollowEnabled, false, 'mouse follow should disable immediately after preference change');
  assert.equal(disabledMouseFollow.targetHeadYaw, 0, 'disabling mouse follow should reset target head yaw');

  const wrap = page.locator('#dh-canvas-wrap');
  const box = await wrap.boundingBox();
  assert.ok(box, 'digital human canvas should expose a drag surface');

  const dragStartX = box.x + box.width * 0.5;
  const dragStartY = box.y + box.height * 0.45;

  const beforeDisabledDrag = await page.evaluate(() => window.__getDigitalHumanDebugState());
  await page.mouse.move(dragStartX, dragStartY);
  await page.mouse.down();
  await page.mouse.move(dragStartX + 110, dragStartY + 20, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(80);

  const afterDisabledDrag = await page.evaluate(() => window.__getDigitalHumanDebugState());
  assert.ok(
    Math.abs((afterDisabledDrag.avatarRotation && afterDisabledDrag.avatarRotation.y || 0) - (beforeDisabledDrag.avatarRotation && beforeDisabledDrag.avatarRotation.y || 0)) < 0.01,
    'avatar rotation should stay nearly unchanged when drag rotate is disabled'
  );

  await page.evaluate(() => {
    window.setDigitalHumanPreferences({
      mouse_follow: false,
      drag_rotate: true,
    });
  });

  const beforeEnabledDrag = await page.evaluate(() => window.__getDigitalHumanDebugState());
  await page.mouse.move(dragStartX, dragStartY);
  await page.mouse.down();
  await page.mouse.move(dragStartX + 110, dragStartY + 20, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(80);

  const afterEnabledDrag = await page.evaluate(() => window.__getDigitalHumanDebugState());
  assert.ok(
    Math.abs((afterEnabledDrag.avatarRotation && afterEnabledDrag.avatarRotation.y || 0) - (beforeEnabledDrag.avatarRotation && beforeEnabledDrag.avatarRotation.y || 0)) > 0.03,
    'avatar rotation should change when drag rotate is enabled'
  );
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
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

  try {
    await bootstrapPage(page, baseUrl);
    await assertAvatarViewportFit(page, 'desktop');

    const initialState = await page.evaluate(() => {
      const root = document.getElementById('dh-floating-root');
      const widget = document.getElementById('dh-widget');
      return {
        rootCount: document.querySelectorAll('#dh-floating-root').length,
        widgetCount: document.querySelectorAll('#dh-widget').length,
        canvasCount: document.querySelectorAll('#dh-canvas-wrap canvas').length,
        rootParentTag: root && root.parentElement ? root.parentElement.tagName : '',
        pose: widget ? widget.dataset.pose : '',
      };
    });

    assert.equal(initialState.rootCount, 1, 'should mount a single floating root');
    assert.equal(initialState.widgetCount, 1, 'should mount a single widget');
    assert.equal(initialState.canvasCount, 1, 'should mount a single canvas');
    assert.equal(initialState.rootParentTag, 'BODY', 'floating root should be mounted to body');
    assert.equal(initialState.pose, 'standby', 'default pose should be standby');

    const panelControls = await page.evaluate(() => ({
      toggleCount: document.querySelectorAll('#dh-sidepanel-toggle').length,
      panelCount: document.querySelectorAll('#dh-sidepanel').length,
    }));
    assert.equal(panelControls.toggleCount, 0, 'default user view should not show the side panel toggle');
    assert.equal(panelControls.panelCount, 0, 'default user view should not render the side panel');

    await assertSpeechBubbleScrollable(page);
    await assertInteractionPreferencesAffectRuntime(page);
    await clickAvatarUntilPoseChanges(page, initialState.pose);

    await page.evaluate(() => {
      if (typeof window.digitalHumanExpand === 'function') window.digitalHumanExpand();
    });
    const menuOpen = await page.evaluate(() => document.getElementById('dh-widget').classList.contains('dh-menu-open'));
    assert.equal(menuOpen, true, 'expand action should open quick menu');

    await page.evaluate(() => {
      if (typeof rerenderCurrentPage === 'function') rerenderCurrentPage();
    });
    await page.waitForSelector('#dh-canvas-wrap canvas');
    const afterRerender = await page.evaluate(() => ({
      rootCount: document.querySelectorAll('#dh-floating-root').length,
      widgetCount: document.querySelectorAll('#dh-widget').length,
      canvasCount: document.querySelectorAll('#dh-canvas-wrap canvas').length,
    }));
    assert.equal(afterRerender.rootCount, 1, 'rerender should keep one floating root');
    assert.equal(afterRerender.widgetCount, 1, 'rerender should keep one widget');
    assert.equal(afterRerender.canvasCount, 1, 'rerender should keep one canvas');

    await page.setViewportSize({ width: 900, height: 1000 });
    await page.waitForTimeout(120);
    await assertAvatarViewportFit(page, 'tablet');

    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(120);
    await assertAvatarViewportFit(page, 'mobile');

    await page.evaluate(async () => {
      if (typeof navigateTo === 'function') await navigateTo('account_settings');
    });
    await page.waitForFunction(() => document.querySelectorAll('#dh-floating-root').length === 0, null, {
      timeout: 5000,
    });
    const afterLeave = await page.evaluate(() => ({
      rootCount: document.querySelectorAll('#dh-floating-root').length,
      widgetCount: document.querySelectorAll('#dh-widget').length,
    }));
    assert.equal(afterLeave.rootCount, 0, 'leaving practice page should destroy floating root');
    assert.equal(afterLeave.widgetCount, 0, 'leaving practice page should destroy widget');

    console.log('digital-human-ui.test.js passed');
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
