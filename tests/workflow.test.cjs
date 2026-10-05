/* Exercise the production folder -> replay -> Codex handoff boundaries with
 * synthetic records. No recordings, database, clipboard, or ASR are used. */
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
  const nodes = new Map();
  const context = {
    current:{item:{id:'sample'},run:{id:'run-old'},review:{body:'old'},segments:[]},
    handoffGeneration:0, navigating:false, boot:{settings:{source:'/old'}},
    $:id => {
      if (!nodes.has(id)) nodes.set(id, {value:'',textContent:'',disabled:false,hidden:false,open:false,
        close() { this.open = false; }});
      return nodes.get(id);
    },
    currentId:() => context.current?.item.id,
    runId:() => context.current?.run?.id || null,
    flushDraft:async () => true,
    openDialog:id => { context.$(id).open = true; },
    formError:(id,error) => { context.$(id).textContent = error?.message || error || ''; },
    toast:() => {}, reportError:error => { throw error; },
    api:async () => { throw new Error('Unexpected API call'); },
    updateWorkspaceLabel:() => {},populateSettings:() => {},showWelcome:() => {},
    ...overrides,
  };
  vm.createContext(context);
  return {context,nodes,load(from,to) { vm.runInContext(section(from,to),context); }};
}
const handoffStart = '  let handoffMemory = null;';
const handoffEnd = "  $('guide-dialog').addEventListener('close'";

test('handoff saves notes first and uses the explicitly selected transcription version', async () => {
  const calls = [];
  const h = harness({flushDraft:async () => { calls.push('save'); return true; },
    api:async url => { calls.push(url); return {prompt:'Synthetic record / run-old / review path',ready:true,warnings:[]}; }});
  h.load(handoffStart,handoffEnd);
  await h.context.openHandoff();
  assert.deepEqual(calls,['save','/api/interviews/sample/handoff?run=run-old']);
  assert.equal(h.context.$('guide-prompt').value,'Synthetic record / run-old / review path');
  assert.equal(h.context.$('guide-copy').disabled,false);
});

test('unsaved notes prevent generating a handoff from stale saved content', async () => {
  const h = harness({flushDraft:async () => false});
  h.load(handoffStart,handoffEnd);
  await h.context.openHandoff();
  assert.equal(h.context.$('guide-dialog').open,false);
  assert.equal(h.context.$('guide-copy').disabled,true);
  assert.equal(h.context.$('guide-prompt').value,'');
});

test('handoff without a transcript exposes material gaps without blocking copying', async () => {
  let requested;
  const h = harness({current:{item:{id:'sample'},run:null},api:async url => {
    requested = url; return {prompt:'Missing transcript; do not invent conclusions.',ready:false,warnings:['尚未转写']};
  }});
  h.load(handoffStart,handoffEnd);
  await h.context.openHandoff();
  assert.equal(requested,'/api/interviews/sample/handoff');
  assert.match(h.context.$('handoff-state').textContent,/尚未转写/);
  assert.equal(h.context.$('guide-copy').disabled,false);
});

test('late handoff response cannot overwrite a different selected transcription', async () => {
  let finish;
  const h = harness({api:async () => new Promise(resolve => { finish = resolve; })});
  h.load(handoffStart,handoffEnd);
  const pending = h.context.openHandoff();
  await new Promise(resolve => setImmediate(resolve));
  h.context.current.run.id = 'run-new';
  finish({prompt:'Old run material',ready:true});
  await pending;
  assert.equal(h.context.$('guide-prompt').value,'');
  assert.equal(h.context.$('guide-copy').disabled,true);
});

const memoryFixture = {root:'/example/output/memory',index_path:'/example/output/memory/MEMORY.md',
  source_directory:'/example/recordings',initialized:false,warnings:[]};

test('opening handoff reads memory but only the explicit action initializes it', async () => {
  const calls = [];
  let initialized = false;
  const h = harness({api:async (url, options) => {
    calls.push([url, options]);
    if (url === '/api/memory/init') {
      assert.equal(options.method,'POST');
      assert.deepEqual({...options.body},{source_directory:memoryFixture.source_directory,root:memoryFixture.root});
      initialized = true; return {...memoryFixture,initialized};
    }
    return {prompt:initialized?'With initialized Memory':'Single review',ready:true,memory:{...memoryFixture,initialized}};
  }});
  h.load(handoffStart,handoffEnd);
  await h.context.openHandoff();
  assert.equal(calls.length,1);
  assert.equal(h.context.$('memory-init').hidden,false);
  await h.context.initializeMemory();
  assert.equal(calls.length,3);
  assert.equal(h.context.$('guide-prompt').value,'With initialized Memory');
  assert.equal(h.context.$('memory-init').hidden,true);
});

test('memory initialization errors retain the review handoff and require an explicit retry', async () => {
  let writes = 0;
  const h = harness({api:async url => {
    if (url === '/api/memory/init') { writes++; throw new Error('资料库已变化'); }
    return {prompt:'Saved review handoff',ready:true,memory:memoryFixture};
  }});
  h.load(handoffStart,handoffEnd);
  await h.context.openHandoff();
  await h.context.initializeMemory();
  assert.equal(writes,1);
  assert.equal(h.context.$('guide-prompt').value,'Saved review handoff');
  assert.equal(h.context.$('guide-copy').disabled,false);
  assert.equal(h.context.$('memory-init').disabled,false);
  assert.match(h.context.$('handoff-error').textContent,/资料库已变化/);
});

test('duplicate initialization clicks and late results cannot overwrite another handoff', async () => {
  let finish, writes = 0;
  const h = harness({api:async url => {
    if (url === '/api/memory/init') { writes++; return new Promise(resolve => { finish = resolve; }); }
    return {prompt:'Old context',ready:true,memory:memoryFixture};
  }});
  h.load(handoffStart,handoffEnd);
  await h.context.openHandoff();
  const pending = h.context.initializeMemory();
  await h.context.initializeMemory();
  assert.equal(writes,1);
  h.context.handoffGeneration++;
  h.context.$('guide-prompt').value = 'New context';
  finish({...memoryFixture,initialized:true});
  await pending;
  assert.equal(h.context.$('guide-prompt').value,'New context');
});

test('unavailable memory can never enable initialization while single review remains usable', async () => {
  const h = harness({api:async () => ({prompt:'Review only',ready:true,
    memory:{...memoryFixture,error:'bound_directory_missing',warnings:['已绑定目录缺失']}})});
  h.load(handoffStart,handoffEnd);
  await h.context.openHandoff();
  assert.equal(h.context.$('memory-init').hidden,true);
  assert.equal(h.context.$('guide-copy').disabled,false);
  assert.match(h.context.$('handoff-memory-status').textContent,/已绑定目录缺失/);
  await h.context.initializeMemory();
});

test('review refresh keeps the selected run, playback context and unsaved draft intact', async () => {
  const calls = [];
  const h = harness({api:async url => {
    calls.push(url); return {review:{body:'New Codex review',exists:true},run:{id:'other'},draft:{body:'remote'}};
  },renderReview:() => calls.push('render')});
  h.context.current.draft = {body:'Local unsaved note'};
  const before = h.context.current;
  h.load('  async function refreshReview()',"  $('review-refresh').addEventListener");
  await h.context.refreshReview();
  assert.equal(h.context.current,before);
  assert.equal(h.context.current.run.id,'run-old');
  assert.equal(h.context.current.draft.body,'Local unsaved note');
  assert.equal(h.context.current.review.body,'New Codex review');
  assert.deepEqual(calls,['/api/interviews/sample?run=run-old','render']);
});

test('changing folder clears the old selection even when new library refresh fails', async () => {
  const calls = [];
  const h = harness({api:async () => { calls.push('settings'); return {settings:{source:'/new'}}; },
    clearCurrentRecord:() => { calls.push('clear'); h.context.current = null; },
    refreshLibrary:async () => { calls.push('library'); throw new Error('Offline'); },
    scan:async () => { calls.push('scan'); }});
  h.load('  async function saveSettings()',"  $('settings-form').addEventListener");
  await h.context.saveSettings();
  assert.deepEqual(calls,['settings','clear','library']);
  assert.equal(h.context.current,null);
  assert.equal(h.context.boot.settings.source,'/new');
  assert.equal(h.context.$('settings-error').textContent,'Offline');
});

test('saving folder scans only after settings and scope refresh complete', async () => {
  const calls = [];
  const h = harness({flushDraft:async () => { calls.push('save draft'); return true; },
    api:async () => { calls.push('settings'); return {settings:{source:'/new'}}; },
    clearCurrentRecord:() => { calls.push('clear'); },
    refreshLibrary:async () => { calls.push('library'); },
    scan:async options => { calls.push(options.openFirst ? 'scan and open' : 'scan'); }});
  h.load('  async function saveSettings()',"  $('settings-form').addEventListener");
  await h.context.saveSettings();
  assert.deepEqual(calls,['save draft','settings','clear','library','scan and open']);
});

test('failed note save prevents changing the configured folder', async () => {
  const h = harness({flushDraft:async () => false});
  h.load('  async function saveSettings()',"  $('settings-form').addEventListener");
  await h.context.saveSettings();
  assert.equal(h.context.boot.settings.source,'/old');
  assert.equal(h.context.current.item.id,'sample');
});

test('an untranscribed interview offers direct replay without creating a transcription task', () => {
  const buttons = [];
  const nodes = new Map();
  const node = () => ({append(){},replaceChildren(){}});
  const replay = () => {};
  const context = vm.createContext({
    $:id => { if (!nodes.has(id)) nodes.set(id,node()); return nodes.get(id); },
    segments:() => [], current:{run:null,media_url:'/api/interviews/sample/media',source_exists:true},
    document:{createElement:node},icon:() => ({}),text:() => ({}),playPause:replay,openTask:() => {},
    actionButton:(label,action) => { buttons.push({label,action}); return {}; },
  });
  vm.runInContext(section('  function renderSegments()','  function setFollow('),context);
  context.renderSegments();
  assert.equal(buttons.find(button => button.label === '播放面试').action,replay);
  assert.ok(buttons.some(button => button.label === '语音转文字'));
});

test('an active scan prevents changing folders until its result settles', async () => {
  const h = harness();
  h.context.$('scan').disabled = true;
  h.load('  async function saveSettings()',"  $('settings-form').addEventListener");
  await h.context.saveSettings();
  assert.equal(h.context.boot.settings.source,'/old');
  assert.match(h.context.$('settings-error').textContent,/仍在扫描/);
});
