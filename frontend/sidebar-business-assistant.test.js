const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const appSource = fs.readFileSync(path.join(__dirname, 'js', 'app.js'), 'utf8');

function extractConstArray(source, name) {
  const marker = `const ${name} = [`;
  const start = source.indexOf(marker);
  if (start === -1) throw new Error(`Cannot find ${name}`);
  const arrayStart = source.indexOf('[', start);
  let depth = 0;
  for (let index = arrayStart; index < source.length; index += 1) {
    const char = source[index];
    if (char === '[') depth += 1;
    if (char === ']') {
      depth -= 1;
      if (depth === 0) return source.slice(arrayStart, index + 1);
    }
  }
  throw new Error(`Cannot extract ${name}`);
}

const sidebarSectionsSource = extractConstArray(appSource, 'SIDEBAR_SECTIONS');
const supportSectionMatch = sidebarSectionsSource.match(
  /id:\s*'support'[\s\S]*?items:\s*\[([^\]]+)\]/
);

assert.ok(supportSectionMatch, 'support sidebar section should exist');
assert.match(
  supportSectionMatch[1],
  /'talent_dashboard'/,
  'risk dashboard should be listed under the business assistant section'
);

const otherSectionsSource = sidebarSectionsSource.replace(supportSectionMatch[0], '');
assert.equal(
  otherSectionsSource.includes("'talent_dashboard'"),
  false,
  'risk dashboard should not appear in any other sidebar section'
);

console.log('sidebar-business-assistant.test.js passed');
