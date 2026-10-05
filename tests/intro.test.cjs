/* Guide actions use synthetic state and never contact a service or start ASR. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
function section(from, to) {
  const start = source.indexOf(from), end = source.indexOf(to, start);
  assert.ok(start >= 0 && end > start, `Missing production boundary: ${from}`);
  return source.slice(start, end);
}
function harness(overrides = {}) {
  const nodes = new Map(), calls = [];
  const buttons = ['trial','full','notes','handoff','review','scan','queue','paths'].map(action => ({dataset:{guideAction:action},disabled:false}));
  const context = {
    current:null, records:[], boot:{settings:{}}, view:'record',
    $:id => {
      if (!nodes.has(id)) nodes.set(id,{value:'',textContent:'',hidden:false,disabled:false,classList:{toggle(){}}});
      return nodes.get(id);
    },
    document:{querySelectorAll:selector => selector === '[data-guide-action="scan"]' ? buttons.filter(button => button.dataset.guideAction === 'scan') : buttons},
    completed:state => ['succeeded','completed','done'].includes(state),
    showView:next => { context.view = next; calls.push(['view',next]); },
    openFolderSetup:() => calls.push(['folder']),
    openDialog:id => calls.push(['dialog',id]),
    openTask:() => { calls.push(['task']); context.$('task-start').value = '0'; context.$('task-limit').value = ''; },
    openHandoff:async () => calls.push(['handoff']),
    toggleNotes:open => calls.push(['notes',open]),
    showNotesTab:tab => calls.push(['tab',tab]),
    refreshQueue:async () => calls.push(['queue']),
    selectRecord:async id => { calls.push(['select',id]); context.current = {item:{id},source_exists:true}; return true; },
    scan:async options => calls.push(['scan',options.openFirst]),
    toast:message => calls.push(['toast',message]),
    api:async () => { throw new Error('The guide must not submit tasks'); },
    ...overrides,
  };
  vm.createContext(context);
  vm.runInContext(section('  function guideState(', '  const guideNavigation ='), context);
  return {context,calls,buttons};
}

test('guide starts with a folder choice or scan and prefers the selected interview', () => {
  const {context:c} = harness();
  assert.equal(c.guideState({},[],null).primary,'choose');
  assert.equal(c.guideState({source_exists:true},[],null).primary,'scan');
  const first = {id:'first',state:'completed'}, selected = {item:{id:'selected'},result_available:false,source_exists:true};
  const state = c.guideState({source_exists:true},[first],selected);
  assert.equal(state.primary,'record');
  assert.equal(state.target,selected.item);
  assert.equal(state.hasTranscript,false);
  assert.equal(state.hasReview,false);
  assert.equal(c.guideState({},[first],null).target,first);
});

test('guide status reflects the selected version and missing source without claiming playback', () => {
  const {context:c,buttons} = harness({records:[{id:'other',state:'completed'}],
    current:{item:{id:'selected',title:'Synthetic interview'},result_available:false,source_exists:false}});
  c.openGuide();
  assert.match(c.$('guide-target').textContent,/当前面试：Synthetic interview/);
  assert.match(c.$('guide-transcribe-status').textContent,/尚无可用转写/);
  assert.match(c.$('guide-import-status').textContent,/原媒体暂不可用/);
  assert.ok(buttons.filter(button => ['trial','full'].includes(button.dataset.guideAction)).every(button => button.disabled));
  assert.equal(buttons.find(button => button.dataset.guideAction === 'notes').disabled,false);
});

test('opening and returning from the guide retains current run and unsaved notes', async () => {
  const selected = {item:{id:'selected'},run:{id:'older-run'},draft:{body:'Unsaved synthetic note'}};
  const {context:c,calls} = harness({current:selected,records:[{id:'first'}]});
  c.$('notes-editor').value = 'Local editor text';
  c.openGuide();
  assert.equal(c.view,'guide');
  await c.runGuideAction('record');
  assert.equal(c.view,'record');
  assert.equal(c.current,selected);
  assert.equal(c.current.run.id,'older-run');
  assert.equal(c.current.draft.body,'Unsaved synthetic note');
  assert.equal(c.$('notes-editor').value,'Local editor text');
  assert.deepEqual(calls,[['view','guide'],['view','record']]);
});

test('guide selects the first available interview only when nothing is selected', async () => {
  const {context:c,calls} = harness({records:[{id:'first'},{id:'second'}]});
  await c.runGuideAction('record');
  assert.deepEqual(calls,[['select','first']]);
  assert.equal(c.current.item.id,'first');
  const failed = harness({records:[{id:'first'}],selectRecord:async () => false});
  await failed.context.runGuideAction('trial');
  await failed.context.runGuideAction('handoff');
  assert.deepEqual(failed.calls,[]);
});

test('trial prefills at most sixty seconds from source duration and opens only the form', async () => {
  for (const [sourceDuration,fallbackDuration,expected] of [[120,120,'60'],[40,40,'40'],[null,8,'60']]) {
    const {context:c,calls} = harness({current:{item:{id:'selected'},source_exists:true,source_duration:sourceDuration,duration:fallbackDuration}});
    await c.runGuideAction('trial');
    assert.equal(c.$('task-start').value,'0');
    assert.equal(c.$('task-limit').value,expected);
    assert.deepEqual(calls,[['view','record'],['task']]);
  }
});

test('full transcription clears a previous trial range and missing media prevents a task', async () => {
  const {context:c,calls} = harness({current:{item:{id:'selected'},source_exists:true}});
  c.$('task-limit').value = '60';
  await c.runGuideAction('full');
  assert.equal(c.$('task-limit').value,'');
  assert.deepEqual(calls,[['view','record'],['task']]);
  const missing = harness({current:{item:{id:'selected'},source_exists:false}});
  await missing.context.runGuideAction('trial');
  assert.equal(missing.calls.some(call => call[0] === 'task'),false);
  assert.equal(missing.calls.at(-1)[0],'toast');
});

test('queue, folder and settings guide actions do not select or modify an interview', async () => {
  const {context:c,calls} = harness({records:[{id:'first'}]});
  await c.runGuideAction('queue');
  await c.runGuideAction('paths');
  await c.runGuideAction('choose');
  assert.equal(c.current,null);
  assert.deepEqual(calls,[['view','queue'],['queue'],['dialog','settings-dialog'],['folder']]);
});

test('note, review and handoff actions open the requested destination for the current interview', async () => {
  const {context:c,calls} = harness({current:{item:{id:'selected'}}});
  await c.runGuideAction('notes');
  await c.runGuideAction('review');
  await c.runGuideAction('handoff');
  assert.deepEqual(calls,[['view','record'],['notes',true],['tab','notes'],['view','record'],['notes',true],['tab','review'],['view','record'],['handoff']]);
});

test('all guide scan controls recover after successful and failed scans', async () => {
  for (const failure of [false,true]) {
    const h = harness({boot:{settings:{source_exists:true}},api:async () => {
      if (failure) throw new Error('Synthetic scan failure');
      return {};
    },refreshLibrary:async () => {},reportError:() => {}});
    vm.runInContext(section('  async function scan(', "  $('scan').addEventListener"),h.context);
    await h.context.scan();
    assert.equal(h.context.$('scan').disabled,false);
    assert.equal(h.context.$('guide-primary').disabled,false);
    assert.equal(h.context.$('guide-scan').disabled,false);
    assert.equal(h.buttons.find(button => button.dataset.guideAction === 'scan').disabled,false);
  }
});
