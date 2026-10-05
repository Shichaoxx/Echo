const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'web/app.js'), 'utf8');
const start = source.indexOf('  function recordDate(');
const end = source.indexOf('  const projectOption = text', start);
assert.ok(start >= 0 && end > start);
const group = vm.runInNewContext('let projects = {p1:{name:"目标岗位"},p2:{name:"空分组"}};\n' + source.slice(start, end) + '; groupRecords');
const plain = value => JSON.parse(JSON.stringify(value));

test('company grouping retains all records and orders interviews by their dates', () => {
  const items = [
    {id:'old', metadata:{company:'Example', date:'2025-12-01'}},
    {id:'unknown', metadata:{}},
    {id:'new', metadata:{company:'Example', date:'2026-01-01'}},
  ];
  const before = JSON.stringify(items);
  const result = plain(group(items, 'company'));
  assert.deepEqual(result.map(value => value.key), ['Example','未标注公司']);
  assert.deepEqual(result[0].items.map(value => value.id), ['new','old']);
  assert.equal(JSON.stringify(items), before);
});
test('month grouping separates years and uses recording date instead of import date', () => {
  const result = plain(group([
    {id:'older', metadata:{date:'2025-09-02'}},
    {id:'recent', metadata:{date:'09-11'}, title:'2026-09-11_10-00-00'},
    {id:'unknown', metadata:{}},
  ], 'month'));
  assert.deepEqual(result.map(value => value.key), ['2026-09','2025-09','未标注时间']);
});
test('missing year is labelled explicitly and ungrouped view is newest first', () => {
  const items = [{id:'partial', metadata:{date:'08-20'}}, {id:'dated', path:'/media/2026-09-01.wav'}];
  const months = plain(group(items, 'month'));
  assert.equal(months[1].label, '8 月 · 年份未标注');
  assert.deepEqual(plain(group(items, 'recent'))[0].items.map(item => item.id), ['dated','partial']);
});
test('interview date takes precedence over recording filename date', () => {
  const result = plain(group([
    {id:'earlier', metadata:{date:'09-22'}, title:'2026-09-21_15-00-00'},
    {id:'later', metadata:{date:'09-23'}, title:'2026-09-21_16-00-00'},
  ], 'month'));
  assert.deepEqual(result[0].items.map(item => item.id), ['later','earlier']);
});

test('project grouping keeps assigned records and empty projects', () => {
  const result = plain(group([
    {id:'assigned', metadata:{project_id:'p1', date:'2026-09-02'}},
    {id:'unassigned', metadata:{date:'2026-09-01'}},
    {id:'invalid', metadata:{project_id:'missing', date:'2026-09-03'}},
  ], 'project'));
  const assigned = result.find(value => value.key === 'p1');
  const empty = result.find(value => value.key === 'p2');
  const ungrouped = result.find(value => value.key === '未分组');
  assert.deepEqual(assigned.items.map(value => value.id), ['assigned']);
  assert.deepEqual(empty.items, []);
  assert.deepEqual(ungrouped.items.map(value => value.id), ['invalid', 'unassigned']);
});
