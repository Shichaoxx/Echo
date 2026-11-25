/* Theme configuration, stored preferences and accessible contrast use production
 * code and CSS tokens. No browser, private content or npm packages are required. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname,'..','web','app.js'),'utf8');
const css = fs.readFileSync(path.join(__dirname,'..','web','styles.css'),'utf8');
function section(from,to) {
  const start=source.indexOf(from),end=source.indexOf(to,start);
  assert.ok(start>=0&&end>start,`Missing production boundary: ${from}`);
  return source.slice(start,end);
}
const palettes = Object.fromEntries([...css.matchAll(/\[data-theme="([\w-]+)"\]\s*\{([^}]+)\}/g)].map(([,id,body]) => [id,
  Object.fromEntries([...body.matchAll(/--([\w-]+):\s*(#[\da-f]{6})(?=;)/gi)].map(([,key,value]) => [key,value]))]));
function harness(stored={}, systemDark=false) {
  const nodes=new Map(),allNodes=[],writes=[];
  function node(tag='div') {
    const value={tagName:tag.toUpperCase(),dataset:{},style:{},children:[],listeners:{},attributes:{},value:'',textContent:'',
      append(...children){this.children.push(...children);},replaceChildren(...children){this.children=children;},
      addEventListener(event,handler){this.listeners[event]=handler;},setAttribute(name,value){this.attributes[name]=value;}};
    Object.defineProperty(value,'id',{set(id){this._id=id;nodes.set(id,this);},get(){return this._id;}});
    allNodes.push(value);return value;
  }
  const $=id=>{if(!nodes.has(id)){const value=node();value.id=id;}return nodes.get(id);};
  const body=node(),root=node(),meta=node(),picker=node();
  const systemTheme={matches:systemDark,addEventListener(event,handler){this[event]=handler;}};
  const context={$,systemTheme,notesWidth:320,sidebarWidth:280,recordGrouping:'company',
    localStorage:{getItem:key=>stored[key]||null,setItem:(key,value)=>writes.push({key,value:JSON.parse(value)})},
    document:{body,documentElement:root,createElement:node,
      querySelector:selector=>selector.startsWith('meta')?meta:picker,
      querySelectorAll:()=>allNodes.filter(item=>item.dataset.themeChoice)},
    getComputedStyle:()=>({getPropertyValue:key=>palettes[body.dataset.theme]?.[key.slice(2)]||''}),
    text:(tag,text,className)=>{const item=node(tag);item.textContent=text;item.className=className;return item;},
    actionButton:(label,action,className)=>{const item=node('button');item.textContent=label;item.className=className;item.addEventListener('click',action);return item;},
  };
  vm.createContext(context);
  vm.runInContext(section("  const preferenceKey =",'  let notesWidth =')+
    section('  function persistAppearance()', '  function openDialog(')+
    '\n globalThis.access={themes,themeOptions,normalizeTheme,resolvedTheme,applyTheme,setTheme,cycleTheme,themeCommands,getTheme:()=>theme};',context);
  context.access.applyTheme();
  return {...context.access,$,body,root,meta,writes,systemTheme};
}
function luminance(hex) {
  const values=[1,3,5].map(start=>parseInt(hex.slice(start,start+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
  return .2126*values[0]+.7152*values[1]+.0722*values[2];
}
function contrast(a,b) {const [light,dark]=[luminance(a),luminance(b)].sort((a,b)=>b-a);return (light+.05)/(dark+.05);}

test('four palettes have preview cards and all selectors also contain the automatic mode',()=>{
  const h=harness();
  const ids = ['light','sand','midnight','dark'];
  assert.deepEqual(Array.from(h.themes, item=>item.id), ids);
  assert.deepEqual(Array.from(h.themes, item=>item.label), ['白蓝','暖白','黑蓝','深空灰']);
  assert.deepEqual(Array.from(h.$('theme-quick').children,item=>item.value), ['system', ...ids]);
  assert.deepEqual(Array.from(h.$('theme-setting').children,item=>item.value), ['system', ...ids]);
  assert.deepEqual(Array.from(h.$('theme-presets').children,item=>item.dataset.themeChoice), ids);
  assert.deepEqual(Array.from(h.$('theme-presets').children,item=>item.dataset.theme), ids);
  assert.equal(h.themes.filter(item=>item.scheme==='light').length,2);
  assert.equal(h.themes.filter(item=>item.scheme==='dark').length,2);
});

test('choosing any theme keeps controls, preview selection, browser scheme and persistence in sync',()=>{
  const h=harness();
  for(const item of h.themeOptions) {
    h.setTheme(item.id);
    const resolved=h.resolvedTheme(item.id,false);
    assert.equal(h.body.dataset.theme,resolved);
    const scheme = item.scheme === 'system' ? 'light' : item.scheme;
    assert.equal(h.root.style.colorScheme,scheme);
    const icon = h.$('app-favicon').href;
    assert.equal(icon.split('?')[0],scheme === 'dark' ? 'favicon-dark.svg' : 'favicon.svg');
    assert.equal(h.$('workspace-logo').src,icon);
    assert.equal(h.$('welcome-logo').src,icon);
    assert.equal(h.$('theme-setting').value,item.id);
    assert.equal(h.$('theme-quick').value,item.id);
    assert.equal(h.meta.content,palettes[resolved].surface);
    const selected=h.$('theme-presets').children.filter(button=>button.attributes['aria-pressed']==='true');
    assert.deepEqual(selected.map(button=>button.dataset.themeChoice),item.id==='system' ? [] : [item.id]);
    assert.equal(h.writes.at(-1).value.theme,item.id);
  }
});

test('system preference follows OS changes without replacing an explicitly selected palette',()=>{
  const h=harness();
  assert.equal(h.body.dataset.theme,'light');
  h.systemTheme.matches=true;h.systemTheme.change();
  assert.equal(h.body.dataset.theme,'midnight');assert.equal(h.getTheme(),'system');
  assert.equal(h.$('app-favicon').href.split('?')[0],'favicon-dark.svg');
  h.setTheme('sand');h.systemTheme.matches=false;h.systemTheme.change();
  assert.equal(h.body.dataset.theme,'sand');assert.equal(h.getTheme(),'sand');
});

test('stored preferences migrate from v2 while current preferences take priority',()=>{
  const old=harness({'echo-v2-appearance':JSON.stringify({theme:'midnight'})});
  assert.equal(old.getTheme(),'midnight');assert.equal(old.body.dataset.theme,'midnight');
  const current=harness({'echo-appearance':JSON.stringify({theme:'dusk'}),'echo-v2-appearance':JSON.stringify({theme:'light'})});
  assert.equal(current.getTheme(),'dark');
  for (const [oldId,id] of Object.entries({sea:'light',cloud:'light',dusk:'dark',forest:'dark',rose:'sand'})) {
    const migrated=harness({'echo-v2-appearance':JSON.stringify({theme:oldId})});
    assert.equal(migrated.getTheme(),id);
    assert.equal(migrated.body.dataset.theme,id);
  }
  assert.equal(harness({'echo-appearance':JSON.stringify({theme:'removed'})}).getTheme(),'system');
});

test('command palette can select every palette and cycles through all choices',()=>{
  const h=harness();
  for(const item of h.themeOptions) {
    h.themeCommands().find(command=>command.title==='切换主题 · '+item.label).action();
    assert.equal(h.getTheme(),item.id);
  }
  h.setTheme('system');
  const visited=[];for(let i=0;i<h.themes.length;i++){h.cycleTheme();visited.push(h.getTheme());}
  assert.deepEqual(visited,['sand','midnight','dark','light']);
});

test('all palettes have complete shared tokens and readable text, state and button colors',()=>{
  const h=harness();
  const required=['surface','surface-raised','sidebar','surface-hover','surface-active','text','text-secondary','text-tertiary','line','line-strong','accent','accent-strong','accent-ink','accent-soft','accent-line','success','danger'];
  for(const item of h.themes) {
    const colors=palettes[item.id];assert.ok(colors,item.id);
    for(const key of required) assert.ok(colors[key],`${item.id}: ${key}`);
    for(const background of ['surface','surface-raised','sidebar','surface-hover','surface-active','accent-soft']) {
      for(const foreground of ['text','text-secondary','text-tertiary']) {
        const ratio=contrast(colors[foreground],colors[background]);
        assert.ok(ratio>=4.5,`${item.id} ${foreground} on ${background}: ${ratio.toFixed(2)}`);
      }
    }
    for(const background of ['surface','surface-raised','accent-soft']) {
      for(const foreground of ['accent','success','danger']) {
        const ratio=contrast(colors[foreground],colors[background]);
        assert.ok(ratio>=4.5,`${item.id} ${foreground} on ${background}: ${ratio.toFixed(2)}`);
      }
    }
    for(const background of ['accent','accent-strong']) {
      const ratio=contrast(colors['accent-ink'],colors[background]);
      assert.ok(ratio>=4.5,`${item.id} button on ${background}: ${ratio.toFixed(2)}`);
    }
  }
});
