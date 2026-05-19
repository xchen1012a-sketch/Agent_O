const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, 'js', 'app.js'), 'utf8');

assert.equal(
  source.includes('员工侧只读浏览已发布文档，支持任意类型文件'),
  true,
  'theory learning helper text should mention arbitrary file types'
);

assert.equal(
  source.includes('<input id="theory-admin-file" type="file" class="hidden">'),
  true,
  'theory learning upload input should not filter file extensions'
);
