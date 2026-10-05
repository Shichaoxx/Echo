/* Run with: node --test tests/frontend-recovery.test.cjs
 * Execute the production recovery/save functions with controlled IO and clocks.
 * No browser globals, private media, or npm packages are needed.
 */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
function section(from, to) {
  const begin = source.indexOf(from), end = source.indexOf(to, begin);
  assert.ok(begin >= 0 && end > begin, `Production function boundaries missing: ${from}`);
  return source.slice(begin, end);
}
const recoveryFunctions = section('  async function requestOnce(', '  function persistAppearance(');
const saveFunctions = section('  function markDraftDirty(', '  async function flushDraft(');
const tick = () => new Promise(resolve => setImmediate(resolve));
const response = (status, body) => ({ok: status >= 200 && status < 300, status,
  json: async () => body, text: async () => JSON.stringify(body)});

function harness({fetch, saveAPI} = {}) {
  const state = {body:'Original note', citations:[], revision:4, dirty:false,
    generation:0, saving:null, error:null, conflict:null};
  const timers = new Map();
  let nextTimer = 0;
  const context = {
    AbortController, Error, SyntaxError,
    fetch: fetch || (async () => { throw new Error('Unexpected fetch'); }),
    drafts:new Map([['sample', state]]), draft:() => state, currentId:() => 'sample',
    updateSaveState:() => {},
    updateWorkspaceLabel:() => {},
    setTimeout(callback, delay) { const id = ++nextTimer; timers.set(id, {callback, delay}); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.createContext(context);
  vm.runInContext(`let boot={csrf:'old'},sessionRefresh=null,sessionVersion=0,saveTimer=null;
    ${recoveryFunctions}
    ${saveFunctions}
    globalThis.testAccess={api,markDraftDirty,saveDraft};`, context);
  if (saveAPI) context.api = saveAPI;
  return {
    ...context.testAccess, state,
    edit(body) { state.body = body; context.testAccess.markDraftDirty(); },
    pending(delay) { return [...timers.values()].filter(timer => timer.delay === delay).length; },
    fire(delay) {
      for (const [id, timer] of [...timers]) if (timer.delay === delay) {
        timers.delete(id); timer.callback();
      }
    },
  };
}

test('GET renews the session once, without an unbounded retry loop', async () => {
  const calls = [];
  const h = harness({fetch:async url => {
    calls.push(url);
    if (url === '/api/bootstrap') return response(200, {csrf:'fresh'});
    return response(401, {});
  }});
  await assert.rejects(h.api('/api/library'), error => error.status === 401);
  assert.deepEqual(calls, ['/api/library', '/api/bootstrap', '/api/library']);
});

test('concurrent safe requests share one session renewal', async () => {
  let requests = 0, renewals = 0, finishRenewal;
  const h = harness({fetch:async url => {
    if (url === '/api/bootstrap') {
      renewals++;
      return new Promise(resolve => { finishRenewal = () => resolve(response(200, {csrf:'fresh'})); });
    }
    return response(++requests <= 2 ? 401 : 200, {ok:true});
  }});
  const pending = Promise.all([h.api('/api/library'), h.api('/api/queue')]);
  await tick();
  assert.equal(renewals, 1);
  finishRenewal();
  await pending;
  assert.equal(requests, 4);
});

test('failed immediate save cancels the old debounce timer and never replays POST', async () => {
  const writes = [];
  const h = harness({fetch:async (url, options) => {
    if (url === '/api/bootstrap') return response(200, {csrf:'fresh'});
    writes.push(options);
    return writes.length === 1 ? response(401, {}) : response(200, {draft:{revision:5}});
  }});
  // This is the conflict-submit path: schedule normal autosave, then save now.
  h.edit('Merged note');
  assert.equal(h.pending(550), 1);
  await assert.rejects(h.saveDraft('sample'), error => error.sessionRecovered === true);
  assert.equal(h.pending(550), 0);
  assert.equal(h.state.body, 'Merged note');
  assert.equal(h.state.revision, 4);
  assert.equal(h.state.dirty, true);
  h.fire(550);
  await tick();
  assert.equal(writes.length, 1);
  // A later, explicit retry uses the recovered session and original revision.
  await h.saveDraft('sample');
  assert.equal(writes.length, 2);
  assert.equal(writes[1].headers['X-Echo-CSRF'], 'fresh');
  assert.equal(JSON.parse(writes[1].body).revision, 4);
  assert.equal(h.state.dirty, false);
  assert.equal(h.state.revision, 5);
});

test('failure cancels a timer created during the request; new input may save later', async () => {
  const writes = [];
  let failFirst;
  const h = harness({saveAPI:async (_url, options) => {
    writes.push(options.body);
    if (writes.length === 1) return new Promise((_resolve, reject) => { failFirst = reject; });
    return {draft:{revision:5}};
  }});
  h.edit('First edit');
  const failedSave = assert.rejects(h.saveDraft('sample'), /disconnected/);
  h.edit('Typed while saving');
  assert.equal(h.pending(550), 1);
  failFirst(new Error('disconnected'));
  await failedSave;
  assert.equal(h.pending(550), 0);
  assert.equal(h.state.body, 'Typed while saving');
  assert.equal(h.state.revision, 4);
  assert.equal(h.state.dirty, true);
  h.fire(550);
  await tick();
  assert.equal(writes.length, 1);
  h.edit('New input after failure');
  h.fire(550);
  await tick();
  assert.equal(writes.length, 2);
  assert.equal(writes[1].body, 'New input after failure');
  assert.equal(writes[1].revision, 4);
  assert.equal(h.state.dirty, false);
});

test('successful save serializes edits made in flight without a duplicate timer write', async () => {
  const writes = [];
  let finishFirst;
  const h = harness({saveAPI:async (_url, options) => {
    writes.push(options.body);
    if (writes.length === 1) return new Promise(resolve => { finishFirst = () => resolve({draft:{revision:5}}); });
    return {draft:{revision:6}};
  }});
  h.edit('First edit');
  const saving = h.saveDraft('sample');
  h.edit('Latest edit');
  finishFirst();
  await saving;
  assert.equal(writes.length, 2);
  assert.deepEqual(writes.map(write => write.revision), [4, 5]);
  assert.deepEqual(writes.map(write => write.body), ['First edit', 'Latest edit']);
  assert.equal(h.pending(550), 0);
  h.fire(550);
  await tick();
  assert.equal(writes.length, 2);
  assert.equal(h.state.revision, 6);
  assert.equal(h.state.dirty, false);
});

test('409 leaves local body, citations, dirty flag and revision intact', async () => {
  const remote = {body:'Another saved note', citations:[], revision:8};
  const h = harness({saveAPI:async () => {
    const error = new Error('conflict'); error.status = 409; error.payload = {draft:remote}; throw error;
  }});
  h.state.citations = [{run_id:'example', index:0, text:'Quoted text', start:0, end:2}];
  h.edit('Local note');
  await assert.rejects(h.saveDraft('sample'), error => error.status === 409);
  assert.equal(h.state.body, 'Local note');
  assert.equal(h.state.citations[0].text, 'Quoted text');
  assert.equal(h.state.revision, 4);
  assert.equal(h.state.dirty, true);
  assert.equal(h.state.conflict, remote);
  assert.equal(h.pending(550), 0);
});

test('request deadline includes stalled response body reading', async () => {
  let aborted = false;
  const h = harness({fetch:async (_url, options) => ({ok:true, status:200,
    json:() => new Promise((_resolve, reject) => options.signal.addEventListener('abort', () => {
      aborted = true; reject(new Error('aborted'));
    })),
  })});
  const failedRead = assert.rejects(h.api('/api/library', {timeoutMs:7}), error => error.timedOut === true);
  await tick();
  h.fire(7);
  await failedRead;
  assert.equal(aborted, true);
});

test('scan and import have a 120 second request deadline', async () => {
  for (const endpoint of ['/api/scan', '/api/import']) {
    const h = harness({fetch:async (_url, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new Error('aborted')));
    })});
    const failedWrite = assert.rejects(h.api(endpoint, {method:'POST', body:{}}), error => error.timedOut === true && error.message.includes('无法确认'));
    assert.equal(h.pending(120000), 1);
    assert.equal(h.pending(15000), 0);
    h.fire(120000);
    await failedWrite;
  }
});
