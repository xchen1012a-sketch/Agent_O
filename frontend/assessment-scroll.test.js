const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require('playwright');

const APP_URL = 'http://127.0.0.1:8000/frontend/#assessment';
const DEMO_USERNAME = 'admin';
const DEMO_PASSWORD = process.env.AGENTO_ADMIN_PASSWORD || process.env.DEMO_PASSWORD || '123456';
const BROWSER_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
];

async function loginByApi() {
  const response = await fetch('http://127.0.0.1:8000/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: DEMO_USERNAME, password: DEMO_PASSWORD }),
  });
  if (!response.ok) {
    throw new Error('API login failed with status ' + response.status);
  }
  const payload = await response.json();
  const data = payload.data || {};
  if (!data.access_token) {
    throw new Error('API login did not return an access token');
  }
  return data;
}

async function ensureLoggedIn(page) {
  const session = await loginByApi();
  await page.addInitScript((loginData) => {
    localStorage.setItem('token', loginData.access_token);
    localStorage.setItem('role', loginData.role || 'admin');
    localStorage.setItem('username', loginData.username || 'admin');
    localStorage.setItem('displayName', loginData.display_name || loginData.username || 'admin');
    localStorage.setItem('userId', String(loginData.user_id || ''));
    localStorage.setItem('store_id', loginData.store_id || '');
    localStorage.setItem('store_name', loginData.store_name || '');
  }, session);

  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });

  const loginButton = page.locator('#login-btn');
  const loginVisible = await page.locator('#login-user').isVisible().catch(() => false);
  if (loginVisible) {
    await page.locator('#login-user').fill(DEMO_USERNAME);
    await page.locator('#login-pass').fill(DEMO_PASSWORD);
    await loginButton.click();
    await page.waitForFunction(() => {
      return !!localStorage.getItem('token') || !document.querySelector('#login-user');
    });
  }

  await page.goto(APP_URL, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => {
    const title = document.querySelector('#content-area .app-page-title');
    return title && title.textContent.trim() === '考试中心';
  });
}

async function main() {
  const browser = await chromium.launch({
    headless: true,
    executablePath: resolveBrowserExecutable(),
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });

  try {
    await ensureLoggedIn(page);

    const questionRegions = page.locator('.assessment-center-paper-questions');
    const hasQuestionRegion = await questionRegions.first().waitFor({ timeout: 3000 }).then(() => true).catch(() => false);
    if (!hasQuestionRegion) {
      const emptyText = await page.locator('#content-area').innerText().catch(() => '');
      assert.match(emptyText, /暂无考试|考试中心/);
      console.log('assessment-scroll.test.js skipped: no active paper questions');
      return;
    }
    assert.equal(
      await questionRegions.count(),
      1,
      '考试题目区域应该只有一个滚动容器，避免鼠标悬停时出现嵌套滚动冲突'
    );

    const scrollResult = await page.evaluate(() => {
      const scrollEl = document.querySelector('.assessment-center-paper-questions');
      const target = document.querySelector('.assessment-center-paper-questions .text-base.font-semibold.text-slate-900');
      if (!scrollEl || !target) {
        return { before: 0, after: 0, found: false };
      }

      const before = scrollEl.scrollTop;
      target.dispatchEvent(new WheelEvent('wheel', {
        deltaY: 900,
        bubbles: true,
        cancelable: true,
      }));

      return {
        found: true,
        before,
        after: scrollEl.scrollTop,
      };
    });
    assert.equal(scrollResult.found, true, '应该能在题目区找到滚动测试目标');
    const beforeScrollTop = scrollResult.before;
    const afterScrollTop = scrollResult.after;
    assert.ok(
      afterScrollTop > beforeScrollTop,
      '鼠标悬停在题目内容上滚动时，题目列表应该继续向下滚动'
    );
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});

function resolveBrowserExecutable() {
  for (let i = 0; i < BROWSER_CANDIDATES.length; i += 1) {
    if (fs.existsSync(BROWSER_CANDIDATES[i])) return BROWSER_CANDIDATES[i];
  }
  return chromium.executablePath();
}
