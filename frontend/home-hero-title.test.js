const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

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

function createLocalStorage(initial = {}) {
  const store = { ...initial };
  return {
    getItem(key) {
      return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
    },
    setItem(key, value) {
      store[key] = String(value);
    },
    removeItem(key) {
      delete store[key];
    },
  };
}

const context = {
  localStorage: createLocalStorage(),
};
vm.createContext(context);
[
  'getAccountDisplayName',
  'getTimeGreetingForHour',
  'isGenericHomeHeroDisplayName',
  'buildHomeHeroTitle',
].forEach((name) => {
  vm.runInContext(extractFunction(appSource, name), context);
});

context.getBeijingHour = () => 20;

context.localStorage = createLocalStorage({
  username: 'admin',
  displayName: '系统管理员',
});
assert.equal(context.buildHomeHeroTitle(), '晚上好，系统管理员');

context.localStorage = createLocalStorage({
  username: 'admin',
  displayName: '管理员',
});
assert.equal(context.buildHomeHeroTitle(), '晚上好，管理员');

context.localStorage = createLocalStorage({
  username: 'u1001',
  displayName: '张三',
});
assert.equal(context.buildHomeHeroTitle(), '晚上好，张三');

console.log('home-hero-title.test.js passed');
