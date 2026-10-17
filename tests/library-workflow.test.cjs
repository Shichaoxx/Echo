const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'..','web','app.js'),'utf8');
function section(from,to) { const start=source.indexOf(from); return source.slice(start,source.indexOf(to,start)); }
class Element {
  constructor(tag,value='') { this.tag=tag;this.textContent=value;this.children=[]; }
  append(...items) { this.children.push(...items); }
  replaceChildren() { this.children=[]; }
  set innerHTML(value) { throw Error('HTML injection is forbidden'); }
}
test('material reader renders headings and tables without executing source HTML', () => {
  const context={text:(tag,value) => new Element(tag,value), document:{createTextNode:value => new Element('text',value)}};
  vm.createContext(context);vm.runInContext(section('  function renderMarkdown(', '  let overviewData'),context);
  const root=new Element('root');
  context.renderMarkdown(root,'# Preparation\n\n<img src=x onerror=alert(1)>\n\n| Task | Status |\n| --- | --- |\n| Practice | Pending |');
  assert.equal(root.children[0].tag,'h2');
  assert.equal(root.children[1].children[0].textContent,'<img src=x onerror=alert(1)>');
  const table=root.children[2].children[0];
  assert.equal(table.tag,'table');assert.equal(table.children.length,2);
});
test('version label distinguishes adopted version without claiming verified full coverage', () => {
  const context={boot:{models:[]},current:{preferred_run_id:'chosen'},stateLabels:{completed:'已完成'},time:x=>String(x)};
  vm.createContext(context);vm.runInContext(section('  function runLabel(', '  function renderRuns('),context);
  assert.match(context.runLabel({id:'chosen',model:'test',state:'completed',options:{start:0,limit:0}},0,2),/采用版本/);
  assert.match(context.runLabel({id:'chosen',model:'test',state:'completed',options:{start:0,limit:0}},0,2),/覆盖待核对/);
  assert.doesNotMatch(context.runLabel({id:'other',model:'test',state:'completed',options:{start:2,limit:3}},1,2),/采用版本/);
});
