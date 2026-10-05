const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web/app.js', 'utf8');
test('scrubbing re-enables follow and reveals text while paused, including silent gaps', () => {
  const paragraphs = [{start:10,end:20},{start:30,end:40}];
  const calls = [];
  const state = vm.createContext({
    loopIndex:0,selected:-1,
    $:()=>({setAttribute(){}}),
    segments:()=>paragraphs,
    setFollow:value=>calls.push(['follow',value]),
    seek:value=>calls.push(['seek',value]),
    updateSegmentHighlight:()=>{},
    scrollToSegment:(index,instant)=>calls.push(['scroll',index,instant]),
  });
  vm.runInContext(source.slice(source.indexOf('  function scrubTo('),source.indexOf("  $('seek').addEventListener('input'")),state);
  state.scrubTo(35);
  assert.equal(state.selected,1);
  assert.equal(state.loopIndex,null);
  assert.deepEqual(calls,[['follow',true],['seek',35],['scroll',1,undefined]]);
  calls.length=0;
  state.scrubTo(22);
  assert.equal(state.selected,0);
  assert.deepEqual(calls[1],['seek',22]);
});
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('  function parsePlaybackTime('), source.indexOf('  function clearClip(')), context);
test('time entry accepts seconds, minutes and hours with fractional seconds', () => {
  for (const [input, expected] of [[' 90 ', 90], ['01:30', 90], ['1:02:03.5', 3723.5], ['0', 0]]) {
    assert.equal(context.parsePlaybackTime(input), expected);
  }
});
test('time entry rejects malformed or ambiguous timestamps', () => {
  for (const input of ['', '-1', '1:60', '1:60:00', '1::2', '1:2:3:4', 'NaN', '1.5:20']) {
    assert.throws(() => context.parsePlaybackTime(input));
  }
});
test('A-B replay uses source timestamps with cached audio offsets and exits on outside seek', () => {
  const nodes = new Map();
  const state = vm.createContext({
    $: id => { if (!nodes.has(id)) nodes.set(id, {style:{setProperty(){}},setAttribute(){}}); return nodes.get(id); },
    audio: {duration:60,currentTime:20,paused:false,readyState:1},
    current:{media_url:'/local-media',duration:160},
    clipLoop:{start:110,end:120},loopIndex:null,activeSegment:-1,following:false,
    offset:()=>100,segments:()=>[],time:String,toast:()=>{},
    updateSegmentHighlight:()=>{},scrollToSegment:()=>{},
  });
  vm.runInContext('function sourceTime(){return audio.currentTime + offset()} function clearClip(){clipLoop=null}',state);
  vm.runInContext(source.slice(source.indexOf('  function updateTime()'),source.indexOf('  async function playPause()')),state);
  state.updateTime();
  assert.equal(state.audio.currentTime,10);
  assert.equal(state.clipLoop.start,110);
  state.audio.paused=true; state.audio.currentTime=20; state.updateTime();
  assert.equal(state.audio.currentTime,20);
  state.seek(130);
  assert.equal(state.clipLoop,null);
  assert.equal(state.audio.currentTime,30);
});
