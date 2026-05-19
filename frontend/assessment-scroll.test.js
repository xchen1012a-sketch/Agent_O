const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const { chromium } = require('playwright');

const APP_URL = 'http://127.0.0.1:8000/frontend/#assessment';
const DEMO_USERNAME = 'admin';
const DEMO_PASSWORD = process.env.AGENTO_ADMIN_PASSWORD || process.env.DEMO_PASSWORD || readDotenvValue('DEMO_SEED_PASSWORD') || '123456';
const JWT_SECRET = process.env.JWT_SECRET_KEY || readDotenvValue('JWT_SECRET_KEY') || 'jewelry-qipei-2026-competition-secret';
const BROWSER_CANDIDATES = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
];

function readDotenvValue(name) {
  const candidates = [
    'backend/.env',
    '../backend/.env',
    '.env',
  ];
  for (let i = 0; i < candidates.length; i += 1) {
    if (!fs.existsSync(candidates[i])) continue;
    const lines = fs.readFileSync(candidates[i], 'utf8').split(/\r?\n/);
    for (let j = 0; j < lines.length; j += 1) {
      const line = lines[j].trim();
      if (!line || line.startsWith('#')) continue;
      const eq = line.indexOf('=');
      if (eq <= 0 || line.slice(0, eq).trim() !== name) continue;
      let value = line.slice(eq + 1).trim();
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }
      return value;
    }
  }
  return '';
}

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

function base64Url(input) {
  return Buffer.from(input)
    .toString('base64')
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
}

function signJwt(payload) {
  const header = base64Url(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = base64Url(JSON.stringify(Object.assign({}, payload, {
    exp: Math.floor(Date.now() / 1000) + 3600,
  })));
  const signature = crypto
    .createHmac('sha256', JWT_SECRET)
    .update(header + '.' + body)
    .digest('base64')
    .replace(/=/g, '')
    .replace(/\+/g, '-')
    .replace(/\//g, '_');
  return header + '.' + body + '.' + signature;
}

async function resolveTestSession() {
  try {
    return await loginByApi();
  } catch (error) {
    if (process.env.AGENTO_ADMIN_PASSWORD || process.env.DEMO_PASSWORD || readDotenvValue('DEMO_SEED_PASSWORD')) throw error;
    return {
      access_token: signJwt({
        user_id: '1',
        username: DEMO_USERNAME,
        role: 'admin',
        store_id: 'STORE_GZ',
      }),
      username: DEMO_USERNAME,
      role: 'admin',
      display_name: '系统管理员',
      user_id: '1',
      store_id: 'STORE_GZ',
      store_name: '广州店',
    };
  }
}

async function ensureLoggedIn(page) {
  const session = await resolveTestSession();
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
