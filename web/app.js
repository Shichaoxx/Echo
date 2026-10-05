/* Echo 0.0. A local interview workspace backed by the authenticated web service. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const audio = $('audio');
  const preferenceKey = 'echo-appearance';
  let prefs = {};
  try { prefs = JSON.parse(localStorage.getItem(preferenceKey) || localStorage.getItem('echo-v2-appearance') || '{}') || {}; } catch (_) { /* Appearance can remain session-only. */ }
  const themes = [
    {id:'system', label:'跟随系统', scheme:'system', description:'随设备的浅色或深色外观切换。'},
    {id:'light', label:'浅色', scheme:'light', description:'暖白纸面与青绿强调，适合白天阅读。'},
    {id:'dark', label:'深色', scheme:'dark', description:'柔和墨色与青绿强调，减少夜间眩光。'},
    {id:'sea', label:'海盐蓝', scheme:'light', description:'清透蓝灰底色，让逐字稿和笔记更清晰。'},
    {id:'sand', label:'暖砂', scheme:'light', description:'温暖米砂纸面，搭配沉静的陶土色。'},
    {id:'dusk', label:'暮山紫', scheme:'dark', description:'灰紫阅读底色，搭配柔和的薰衣草色。'},
    {id:'midnight', label:'午夜蓝', scheme:'dark', description:'深海蓝底色，搭配明亮而克制的冰蓝色。'},
  ];
  function normalizeTheme(value) {
    const aliases = {cloud:'sea', rose:'sand', forest:'dark'};
    const id = Object.hasOwn(aliases, value) ? aliases[value] : value;
    return themes.some(item => item.id === id) ? id : 'system';
  }
  function resolvedTheme(value, systemDark) {
    const id = normalizeTheme(value);
    return id === 'system' ? (systemDark ? 'dark' : 'light') : id;
  }
  let theme = normalizeTheme(prefs.theme);
  let notesWidth = Math.max(260, Math.min(440, Number(prefs.notesWidth) || 320));
  let sidebarWidth = Math.max(220, Math.min(480, Number(prefs.sidebarWidth) || 280));
  let recordGrouping = ['company','month','recent','project'].includes(prefs.recordGrouping) ? prefs.recordGrouping : 'company';
  let projects = {};
  const collapsedGroups = new Set();
  let boot = null, records = [], current = null, view = 'record', navigating = false;
  let following = true, activeSegment = -1, selected = -1, loopIndex = null, pendingSeek = null;
  let clipLoop = null, videoOpen = false, waveformController = null, waveformGeneration = 0;
  let toastTimer, saveTimer, queueTimer, queueRequest = null, queueVersion = 0, queueFailures = 0;
  let sessionRefresh = null, sessionVersion = 0, initializing = false;
  let matches = [], matchCursor = -1, commands = [], commandCursor = 0, exportGeneration = 0, handoffGeneration = 0;
  let jobs = [], previousJobStates = new Map(), jobsRenderKey = '', exportContent = '', exportExtension = 'md', mediaFallback = false;
  const drafts = new Map(), positions = new Map();
  const systemTheme = matchMedia('(prefers-color-scheme: dark)');
  const escapeEditable = el => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));
  const segments = () => current?.segments || [];
  const currentId = () => current?.item?.id;
  const runId = () => current?.run?.id || current?.run?.run_id || null;
  const draft = () => drafts.get(currentId());
  const offset = () => Math.max(0, Number(current?.media_offset) || 0);
  const sourceTime = () => offset() + (audio.currentTime || 0);
  const stateLabels = {queued:'等待中',running:'正在处理',succeeded:'已完成',completed:'已完成',done:'已完成',failed:'失败',cancelled:'已取消',canceled:'已取消',pending:'待转写',ready:'可回听',imported:'待转写'};
  const completed = state => ['succeeded', 'completed', 'done'].includes(state);
  const time = seconds => { const n = Math.max(0, Math.floor(Number(seconds) || 0)); return n >= 3600 ? `${Math.floor(n / 3600)}:${String(Math.floor(n / 60) % 60).padStart(2,'0')}:${String(n % 60).padStart(2,'0')}` : `${String(Math.floor(n / 60)).padStart(2,'0')}:${String(n % 60).padStart(2,'0')}`; };
  const text = (tag, value, className) => { const el = document.createElement(tag); el.textContent = value; if (className) el.className = className; return el; };
  const icon = name => { const el = document.createElementNS('http://www.w3.org/2000/svg','svg'); el.classList.add('icon'); el.setAttribute('aria-hidden','true'); const use = document.createElementNS('http://www.w3.org/2000/svg','use'); use.setAttribute('href','#i-' + name); el.append(use); return el; };
  function actionButton(label, action, className = 'quiet-button') { const el = text('button', label, className); el.type = 'button'; el.addEventListener('click', action); return el; }
  function toast(message) { clearTimeout(toastTimer); $('toast').textContent = message; $('toast').hidden = false; toastTimer = setTimeout(() => $('toast').hidden = true, 4200); }
  function formError(id, error) { $(id).textContent = error?.message || error || ''; $(id).hidden = !error; }
  function reportError(error) { $('global-error-text').textContent = error?.message || String(error); $('global-error').hidden = false; }
  async function requestOnce(path, {method = 'GET', body, responseText = false, timeoutMs, signal, ...options} = {}) {
    const controller = new AbortController();
    const deadline = timeoutMs ?? (['/api/scan', '/api/import'].includes(path) ? 120000 : 15000);
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, deadline);
    const abort = () => controller.abort();
    if (signal?.aborted) controller.abort(); else signal?.addEventListener('abort', abort, {once:true});
    try {
      const response = await fetch(path, {method, credentials:'same-origin', cache:'no-store', ...options, signal:controller.signal,
        headers:{...(body !== undefined ? {'Content-Type':'application/json'} : {}), ...(method !== 'GET' ? {'X-Echo-CSRF':boot?.csrf || ''} : {}), ...options.headers},
        ...(body !== undefined ? {body:JSON.stringify(body)} : {})});
      if (!response.ok) {
        let payload = {};
        try { payload = await response.json(); }
        catch (error) { if (controller.signal.aborted) throw error; /* Non-JSON errors retain their HTTP status. */ }
        const error = new Error(payload.error || (response.status === 401 || response.status === 403 ? '连接授权已过期，请重新连接 Echo。' : `请求失败（${response.status}），请重试。`));
        error.status = response.status; error.payload = payload; throw error;
      }
      // Keep the deadline active while reading the body, not only the headers.
      return await (responseText ? response.text() : response.json());
    } catch (error) {
      if (error.status) throw error;
      const safe = method === 'GET' || method === 'HEAD';
      const reason = timedOut ? '本机服务响应超时。' : controller.signal.aborted ? '请求已中止。' : error instanceof SyntaxError ? '本机服务返回了无法读取的内容。' : '无法连接本机服务。';
      const failure = new Error(reason + (safe ? '当前内容已保留，请确认服务正在运行后重试。' : '当前内容已保留，无法确认本次操作是否已提交；恢复连接后请检查状态再重试。'));
      failure.connectionFailure = true; failure.timedOut = timedOut; throw failure;
    } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
  }
  function renewSession() {
    if (sessionRefresh) return sessionRefresh;
    sessionRefresh = requestOnce('/api/bootstrap').then(value => { boot = value; sessionVersion++; updateWorkspaceLabel(); return value; }).finally(() => { sessionRefresh = null; });
    return sessionRefresh;
  }
  async function api(path, options = {}) {
    const method = (options.method || 'GET').toUpperCase(), safe = method === 'GET' || method === 'HEAD';
    const version = sessionVersion, request = {...options, method};
    try { return await requestOnce(path, request); }
    catch (error) {
      if (![401,403].includes(error.status) || path === '/api/bootstrap') throw error;
      try { if (version === sessionVersion) await renewSession(); }
      catch (reconnectError) {
        if (!safe) reconnectError.message = '连接已失效，暂时无法恢复。当前内容已保留，本次操作未自动重发；请恢复连接后检查状态再重试。';
        throw reconnectError;
      }
      if (safe) return requestOnce(path, request);
      // A write is never replayed: its first outcome can be uncertain. Notes keep
      // their original revision so the next explicit retry can detect conflicts.
      error.message = '连接已恢复。当前内容已保留，本次操作未自动重发；请检查保存或任务状态后再重试。';
      error.sessionRecovered = true; throw error;
    }
  }
  function persistAppearance() { try { localStorage.setItem(preferenceKey, JSON.stringify({theme, notesWidth, sidebarWidth, recordGrouping})); } catch (_) { /* No private content is stored here. */ } }
  function applyTheme() {
    const resolved = resolvedTheme(theme, systemTheme.matches);
    document.body.dataset.theme = resolved;
    document.documentElement.style.colorScheme = themes.find(item => item.id === resolved).scheme;
    $('theme-setting').value = theme;
    $('theme-quick').value = theme;
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === theme));
      button.dataset.theme = resolvedTheme(button.dataset.themeChoice, systemTheme.matches);
    });
    $('theme-description').textContent = themes.find(item => item.id === theme).description;
    document.querySelector('meta[name="theme-color"]').content = getComputedStyle(document.body).getPropertyValue('--surface').trim();
  }
  function setTheme(value) { theme = normalizeTheme(value); applyTheme(); persistAppearance(); }
  function cycleTheme() { setTheme(themes[(themes.findIndex(item => item.id === theme) + 1) % themes.length].id); }
  function themeCommands() {
    return [
      {title:'切换到下一套主题', hint:'外观', action:cycleTheme},
      ...themes.map(item => ({title:'切换主题 · ' + item.label, hint:item.scheme === 'system' ? '外观 · 跟随系统' : item.scheme === 'light' ? '外观 · 浅色配色' : '外观 · 深色配色', action:() => setTheme(item.id)})),
    ];
  }
  const themeQuick = document.createElement('select'); themeQuick.id = 'theme-quick'; themeQuick.setAttribute('aria-label', '主题');
  document.querySelector('.theme-picker').replaceChildren(themeQuick);
  for (const select of [themeQuick, $('theme-setting')]) {
    select.replaceChildren();
    themes.forEach(({id,label}) => { const option = text('option',label); option.value = id; select.append(option); });
  }
  themes.forEach(({id,label,description}) => {
    const button = actionButton('', () => setTheme(id), 'theme-card');
    button.dataset.themeChoice = id; button.setAttribute('aria-label', label + '主题'); button.title = description;
    const preview = text('span', '', 'theme-preview'); preview.setAttribute('aria-hidden', 'true');
    const pane = text('span', '', 'theme-preview-pane');
    pane.append(text('span', '', 'theme-preview-line'), text('span', '', 'theme-preview-line'), text('span', '', 'theme-preview-accent'));
    preview.append(text('span', '', 'theme-preview-sidebar'), pane);
    button.append(preview, text('span', label, 'theme-card-label')); $('theme-presets').append(button);
  });
  themeQuick.addEventListener('change', event => setTheme(event.target.value));
  systemTheme.addEventListener('change', applyTheme);
  $('theme-setting').addEventListener('change', event => setTheme(event.target.value));
  function openDialog(id) {
    const dialog = $(id); if (!dialog.open) dialog.showModal();
    if (id === 'command-dialog') { $('command-input').value = ''; renderCommands(); $('command-input').focus(); }
    $('record-more').open = false;
    if (id === 'settings-dialog') populateSettings();
    if (id === 'import-dialog') { formError('import-error', null); $('import-paths').focus(); }
  }
  function enableBackdropDismiss(dialog) {
    let startedOutside = false;
    const outside = event => {
      const rect = dialog.getBoundingClientRect();
      return event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom);
    };
    dialog.addEventListener('pointerdown', event => { startedOutside = event.button === 0 && outside(event); });
    dialog.addEventListener('pointercancel', () => { startedOutside = false; });
    dialog.addEventListener('close', () => { startedOutside = false; });
    dialog.addEventListener('click', event => {
      const dismiss = startedOutside && outside(event);
      startedOutside = false;
      if (dismiss) { event.preventDefault(); event.stopPropagation(); dialog.close(); }
    });
  }
  document.querySelectorAll('dialog').forEach(enableBackdropDismiss);
  document.addEventListener('click', event => {
    const menu = $('record-more');
    if (menu.open && !menu.contains(event.target)) menu.open = false;
  });
  document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
  document.querySelectorAll('[data-import]').forEach(button => button.addEventListener('click', () => openDialog('import-dialog')));
  document.querySelectorAll('[data-select-folder]').forEach(button => button.addEventListener('click', openFolderSetup));
  document.querySelectorAll('[data-settings]').forEach(button => button.addEventListener('click', () => openDialog('settings-dialog')));
  $('settings-open').addEventListener('click', () => openDialog('settings-dialog'));
  $('workspace-switch').addEventListener('click', openFolderSetup);
  function updateWorkspaceLabel() {
    const path = boot?.settings?.source || '';
    $('workspace-name').textContent = path.split(/[\\/]/).filter(Boolean).pop() || '我的资料库';
    $('workspace-switch').title = path || '选择输入文件夹';
    $('workspace-switch').setAttribute('aria-label', '面试文件夹：' + $('workspace-name').textContent + '，点击选择');
  }
  $('launch-search').addEventListener('click', () => openDialog('command-dialog'));
  $('global-dismiss').addEventListener('click', () => $('global-error').hidden = true);
  $('global-retry').addEventListener('click', () => initialize());
  function showView(next) {
    view = next; $('queue-view').hidden = next !== 'queue';
    $('welcome-view').hidden = next !== 'guide' && (next === 'queue' || !!current);
    ['record-header','workspace','player'].forEach(id => $(id).hidden = next !== 'record' || !current);
    $('video-stage').hidden = next !== 'record' || !current || !videoOpen;
    if (next !== 'record') audio.pause();
    $('breadcrumb-current').textContent = next === 'queue' ? '处理队列' : next === 'guide' || !current ? '使用指南' : '资料库';
    document.querySelectorAll('[data-view]').forEach(button => button.classList.toggle('active', button.dataset.view === (next === 'record' && !current ? 'guide' : next)));
    $('shell').classList.remove('sidebar-mobile-open');
  }
  document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => { showView(button.dataset.view); if (view === 'queue') refreshQueue(); }));
  $('sidebar-stow').addEventListener('click', () => { $('shell').classList.remove('sidebar-mobile-open'); if (!matchMedia('(max-width:700px)').matches) $('shell').classList.add('sidebar-hidden'); });
  $('sidebar-toggle').addEventListener('click', () => { $('shell').classList.toggle(matchMedia('(max-width:700px)').matches ? 'sidebar-mobile-open' : 'sidebar-hidden'); });
  function applySidebarWidth() {
    const maximum = Math.max(220, Math.min(480, document.documentElement.clientWidth - 440));
    const width = Math.min(sidebarWidth, maximum);
    $('shell').style.setProperty('--sidebar-width', width + 'px');
    $('sidebar-splitter').setAttribute('aria-valuenow', String(width));
    $('sidebar-splitter').setAttribute('aria-valuemax', String(maximum));
  }
  let sidebarDrag = null;
  $('sidebar-splitter').addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    sidebarDrag = event.pointerId; event.currentTarget.setPointerCapture(sidebarDrag);
    document.body.classList.add('resizing'); event.preventDefault();
  });
  $('sidebar-splitter').addEventListener('pointermove', event => {
    if (event.pointerId !== sidebarDrag) return;
    sidebarWidth = Math.max(220, Math.min(480, document.documentElement.clientWidth - 440, Math.round(event.clientX - $('shell').getBoundingClientRect().left)));
    applySidebarWidth();
  });
  const finishSidebarResize = () => { if (sidebarDrag !== null) { sidebarDrag = null; document.body.classList.remove('resizing'); persistAppearance(); } };
  ['pointerup','pointercancel','lostpointercapture'].forEach(name => $('sidebar-splitter').addEventListener(name, finishSidebarResize));
  $('sidebar-splitter').addEventListener('dblclick', () => { sidebarWidth = 280; applySidebarWidth(); persistAppearance(); });
  $('sidebar-splitter').addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const maximum = Number($('sidebar-splitter').getAttribute('aria-valuemax'));
    const currentWidth = Number($('sidebar-splitter').getAttribute('aria-valuenow'));
    sidebarWidth = event.key === 'Home' ? 220 : event.key === 'End' ? maximum : Math.max(220, Math.min(maximum, currentWidth + (event.key === 'ArrowRight' ? 20 : -20)));
    applySidebarWidth(); persistAppearance();
  });
  window.addEventListener('resize', applySidebarWidth);
  applySidebarWidth();
  function notesVisible() { return matchMedia('(max-width:1050px)').matches ? $('main').classList.contains('notes-mobile-open') : !$('workspace').classList.contains('notes-hidden'); }
  function toggleNotes(force) {
    const open = typeof force === 'boolean' ? force : !notesVisible();
    $('workspace').classList.toggle('notes-hidden', !open); $('main').classList.toggle('notes-mobile-open', open);
    $('notes-toggle').setAttribute('aria-expanded', String(open));
    if (!open && $('notes-pane').contains(document.activeElement)) $('notes-toggle').focus();
  }
  function showNotesTab(tab, {focus = false} = {}) {
    for (const name of ['notes', 'review']) {
      const active = name === tab;
      $(name + '-content').hidden = !active;
      $(name + '-tab').setAttribute('aria-selected', String(active));
      $(name + '-tab').tabIndex = active ? 0 : -1;
    }
    if (focus) $(tab + '-tab').focus();
  }
  for (const name of ['notes', 'review']) {
    $(name + '-tab').addEventListener('click', () => showNotesTab(name));
    $(name + '-tab').addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      showNotesTab(event.key === 'Home' ? 'notes' : event.key === 'End' ? 'review' : name === 'notes' ? 'review' : 'notes', {focus:true});
    });
  }
  $('notes-toggle').addEventListener('click', () => toggleNotes()); $('notes-close').addEventListener('click', () => toggleNotes(false));
  matchMedia('(max-width:1050px)').addEventListener('change', () => $('notes-toggle').setAttribute('aria-expanded', String(notesVisible())));
  function updateWidth(width) { notesWidth = Math.max(260, Math.min(440, Math.round(width))); $('workspace').style.setProperty('--notes-width', notesWidth + 'px'); $('splitter').setAttribute('aria-valuenow', String(notesWidth)); }
  let dragId = null;
  $('splitter').addEventListener('pointerdown', event => { dragId = event.pointerId; event.currentTarget.setPointerCapture(dragId); document.body.classList.add('resizing'); event.preventDefault(); });
  $('splitter').addEventListener('pointermove', event => { if (event.pointerId === dragId) updateWidth($('workspace').getBoundingClientRect().right - event.clientX); });
  const endResize = () => { if (dragId !== null) { dragId = null; document.body.classList.remove('resizing'); persistAppearance(); } };
  $('splitter').addEventListener('pointerup', endResize); $('splitter').addEventListener('pointercancel', endResize);
  $('splitter').addEventListener('keydown', event => { if (['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) { event.preventDefault(); updateWidth(event.key === 'Home' ? 260 : event.key === 'End' ? 440 : notesWidth + (event.key === 'ArrowLeft' ? 20 : -20)); persistAppearance(); } });

  function newDraft(value = {}) { return {body:value.body || '', citations:Array.isArray(value.citations) ? value.citations : [], revision:value.revision ?? 0, dirty:false, generation:0, saving:null, error:null, conflict:null}; }
  function updateSaveState() {
    const state = draft(); if (!state) return;
    $('save-state').textContent = state.error ? '尚未保存' : state.saving ? '保存中…' : state.dirty ? '等待保存…' : '已保存到本机';
    $('save-state').dataset.state = state.error ? 'error' : '';
    $('save-error').hidden = !state.error;
    $('save-error-text').textContent = state.conflict ? '另一处更新了笔记。你的内容仍在这里，请合并后再切换记录。' : state.error?.message || '';
    $('save-conflict').hidden = !state.conflict; $('save-retry').hidden = !!state.conflict;
  }
  function markDraftDirty() {
    const state = draft(); if (!state) return;
    state.generation++; state.dirty = true; updateSaveState(); clearTimeout(saveTimer);
    const id = currentId(); saveTimer = setTimeout(() => { saveTimer = null; saveDraft(id).catch(() => {}); }, 550);
  }
  async function saveDraft(id) {
    if (id === currentId()) { clearTimeout(saveTimer); saveTimer = null; }
    const state = drafts.get(id); if (!state || !state.dirty) return;
    if (state.saving) { await state.saving; if (state.dirty) return saveDraft(id); return; }
    if (state.conflict) throw state.error;
    const generation = state.generation;
    const payload = {body:state.body, citations:state.citations.map(citation => ({...citation})), revision:state.revision};
    state.error = null;
    state.saving = (async () => {
      try {
        const response = await api(`/api/interviews/${encodeURIComponent(id)}/draft`, {method:'POST', body:payload});
        state.revision = response.draft.revision;
        if (state.generation === generation) state.dirty = false;
      } catch (error) {
        // A newer edit may have scheduled a timer while this request was in
        // flight. Cancel that residual retry; input after the failure may create
        // a fresh save, while explicit retry retains the original revision.
        if (id === currentId()) { clearTimeout(saveTimer); saveTimer = null; }
        state.error = error;
        if (error.status === 409 && error.payload?.draft) state.conflict = error.payload.draft;
        throw error;
      } finally { state.saving = null; if (id === currentId()) updateSaveState(); }
    })();
    updateSaveState(); await state.saving;
    if (state.dirty) return saveDraft(id);
  }
  async function flushDraft() {
    clearTimeout(saveTimer);
    try {
      if (currentId()) await saveDraft(currentId());
      for (const [id, state] of drafts) if (id !== currentId() && (state.dirty || state.saving)) await saveDraft(id);
      return true;
    }
    catch (_) { toggleNotes(true); showNotesTab('notes'); updateSaveState(); toast('笔记尚未保存，已保留在当前页面。请重试或合并后继续。'); return false; }
  }
  window.echoFlushPendingChanges = flushDraft;
  window.addEventListener('beforeunload', event => { if ([...drafts.values()].some(state => state.dirty || state.saving)) { event.preventDefault(); event.returnValue = ''; } });
  $('notes-editor').addEventListener('input', event => { if (!draft()) return; draft().body = event.target.value; markDraftDirty(); });
  $('save-retry').addEventListener('click', () => flushDraft());
  $('save-export').addEventListener('click', () => openExport('notes'));
  $('save-conflict').addEventListener('click', () => {
    const state = draft(); if (!state?.conflict) return;
    $('conflict-editor').value = state.body === state.conflict.body ? state.body : `${state.body}\n\n--- 另一处保存的内容（请整理后保存） ---\n\n${state.conflict.body || ''}`;
    formError('conflict-error', null); openDialog('conflict-dialog');
  });
  $('conflict-submit').addEventListener('click', async () => {
    const state = draft(); if (!state) return;
    const all = [...state.citations, ...(state.conflict?.citations || [])], seen = new Set();
    state.citations = all.filter(citation => { const key = `${citation.run_id}:${citation.index}:${citation.start}:${citation.text}`; if (seen.has(key)) return false; seen.add(key); return true; });
    if (state.conflict) state.revision = state.conflict.revision;
    state.conflict = null; state.error = null; state.body = $('conflict-editor').value;
    $('notes-editor').value = state.body; markDraftDirty(); renderCitations(); $('conflict-submit').disabled = true;
    try { await saveDraft(currentId()); $('conflict-dialog').close(); toast('合并后的笔记已保存'); }
    catch (error) {
      if (state.conflict && state.conflict.body !== state.body) {
        $('conflict-editor').value = `${state.body}\n\n--- 刚收到的更新（请继续整理） ---\n\n${state.conflict.body || ''}`;
        formError('conflict-error', '合并期间另一处再次保存了内容。最新正文已补在下方，请继续整理后保存。');
      } else formError('conflict-error', error);
    }
    finally { $('conflict-submit').disabled = false; }
  });

  function recordMeta(record) {
    const metadata = record.metadata || {};
    const known = records.find(item => item.id === record.id);
    const state = record.state || known?.state || (record.id === currentId() ? current?.run?.state : null);
    return [metadata.company, metadata.round, metadata.date, record.duration ? time(record.duration) : '', stateLabels[state] || (record.latest_run || known?.latest_run ? '已有转写' : '待转写')].filter(Boolean).join(' · ');
  }
  function runLabel(run, index, total) {
    const key = run.model || run.options?.model;
    const model = boot?.models?.find(item => item.id === key);
    const modelName = model?.name?.split(' · ')[0] || key?.split('/').pop() || `版本 ${total - index}`;
    const rawDate = run.started_at || run.created_at || run.created || run.at;
    const parsed = rawDate ? new Date(rawDate) : null;
    const when = parsed && !Number.isNaN(parsed.getTime()) ? parsed.toLocaleString('zh-CN', {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}) : rawDate ? String(rawDate).slice(0,16).replace('T',' ') : '';
    const options = run.options || {}, start = Number(options.start) || 0;
    const range = options.limit ? `${time(start)}–${time(start + Number(options.limit))}` : start ? `${time(start)} 起` : '完整录音';
    return [modelName, when, range, stateLabels[run.state] || run.state].filter(Boolean).join(' · ');
  }
  function renderRuns() {
    $('run-select').replaceChildren();
    (current.runs || []).forEach((run, index) => { const option = text('option', runLabel(run, index, current.runs.length)); option.value = run.id || run.run_id; $('run-select').append(option); });
    if (!runId()) { const option = text('option', current.runs?.length ? '选择转写版本' : '尚未转写'); option.value = ''; $('run-select').prepend(option); }
    $('run-select').value = runId() || ''; $('run-select').disabled = !current.runs?.length;
  }
  function recordDate(record) {
    const date = String(record.metadata?.date || '');
    const recorded = String(record.title || '').match(/\d{4}-\d{2}-\d{2}/)?.[0] || String(record.path || '').match(/\d{4}-\d{2}-\d{2}/)?.[0];
    return date.match(/^\d{4}-\d{2}-\d{2}/)?.[0] || (/^\d{2}-\d{2}$/.test(date) ? (recorded?.slice(0,4) || '0000') + '-' + date : recorded || '');
  }
  function groupRecords(items, mode) {
    const sorted = [...items].sort((a,b) => recordDate(b).localeCompare(recordDate(a)) || String(a.title || '').localeCompare(String(b.title || ''), 'zh-CN'));
    if (mode === 'recent') return [{key:'all', label:'全部记录', items:sorted}];
    const groups = new Map();
    if (mode === 'project') Object.entries(projects).forEach(([key, project]) => groups.set(key, {key, label:project.name, items:[]}));
    for (const record of sorted) {
      const date = recordDate(record);
      const key = mode === 'project' ? (projects[record.metadata?.project_id] ? record.metadata.project_id : '未分组') : mode === 'company' ? String(record.metadata?.company || '').trim() || '未标注公司' : date ? date.slice(0,7) : '未标注时间';
      const label = mode === 'project' ? projects[key]?.name || '未分组' : mode === 'month' && date ? key.startsWith('0000-') ? `${Number(key.slice(5))} 月 · 年份未标注` : `${key.slice(0,4)} 年 ${Number(key.slice(5))} 月` : key;
      if (!groups.has(key)) groups.set(key, {key, label, items:[]});
      groups.get(key).items.push(record);
    }
    return [...groups.values()].sort((a,b) => {
      const unknownA = a.key.startsWith('未标注') || a.key === '未分组', unknownB = b.key.startsWith('未标注') || b.key === '未分组';
      if (unknownA !== unknownB) return unknownA ? 1 : -1;
      return ['company','project'].includes(mode) ? a.label.localeCompare(b.label, 'zh-CN') : b.key.localeCompare(a.key);
    });
  }
  const projectOption = text('option', '自定义项目分组'); projectOption.value = 'project'; $('record-grouping').append(projectOption);
  const projectDialog = document.createElement('dialog'); projectDialog.className = 'glass-dialog'; projectDialog.id = 'projects-dialog';
  projectDialog.setAttribute('aria-labelledby', 'projects-heading');
  projectDialog.innerHTML = '<form id="projects-form"><div class="dialog-header"><h2 id="projects-heading">项目分组</h2><button type="button" id="projects-close" aria-label="关闭项目分组">×</button></div><div class="dialog-body"><p class="helper">为求职方向、面试阶段或练习专题创建分组。在记录信息中选择归属；分组不会移动录屏。</p><label class="field">选择分组<select id="project-edit"></select></label><label class="field">分组名称<input id="project-name" maxlength="80" required></label><p class="form-error" id="project-error" role="alert" hidden></p><p class="helper">删除分组后，记录回到未分组，文件和笔记保留。</p></div><div class="dialog-footer"><button type="button" id="project-delete">删除分组</button><button class="primary" type="submit">保存分组</button></div></form>';
  document.body.append(projectDialog);
  const projectField = document.createElement('label'); projectField.className = 'field'; projectField.textContent = '项目分组';
  const projectSelect = document.createElement('select'); projectSelect.id = 'meta-project_id'; projectField.append(projectSelect);
  $('meta-tags').closest('label').after(projectField);
  function projectOptions(select, emptyLabel) {
    select.replaceChildren(); const empty = text('option', emptyLabel); empty.value = ''; select.append(empty);
    Object.entries(projects).forEach(([id, project]) => { const option = text('option', project.name); option.value = id; select.append(option); });
  }
  const manageProjects = actionButton('管理项目分组', () => { projectOptions($('project-edit'), '新建分组'); $('project-name').value = ''; $('project-delete').disabled = true; formError('project-error', null); openDialog('projects-dialog'); }, 'quiet-button');
  $('record-grouping').after(manageProjects);
  $('projects-close').addEventListener('click', () => projectDialog.close());
  $('project-edit').addEventListener('change', () => { const id = $('project-edit').value; $('project-name').value = projects[id]?.name || ''; $('project-delete').disabled = !id; });
  async function saveProject(operation) {
    try {
      await api('/api/projects', {method:'POST', body:{operation, id:$('project-edit').value, name:$('project-name').value}});
      recordGrouping = 'project'; $('record-grouping').value = recordGrouping; persistAppearance(); await refreshLibrary(); projectDialog.close(); toast('项目分组已更新');
    } catch (error) { formError('project-error', error); }
  }
  $('projects-form').addEventListener('submit', event => { event.preventDefault(); saveProject($('project-edit').value ? 'rename' : 'create'); });
  $('project-delete').addEventListener('click', () => saveProject('delete'));
  $('record-grouping').value = recordGrouping;
  $('record-grouping').addEventListener('change', event => { recordGrouping = event.target.value; renderRecords(); persistAppearance(); });
  function updateGroupToggle() {
    const keys = groupRecords(records, recordGrouping).map(group => recordGrouping + ':' + group.key);
    $('groups-toggle').textContent = keys.length && keys.every(key => collapsedGroups.has(key)) ? '全部展开' : '全部折叠';
    $('groups-toggle').disabled = !keys.length;
  }
  $('groups-toggle').addEventListener('click', () => {
    const keys = groupRecords(records, recordGrouping).map(group => recordGrouping + ':' + group.key);
    const expand = keys.every(key => collapsedGroups.has(key));
    keys.forEach(key => expand ? collapsedGroups.delete(key) : collapsedGroups.add(key));
    renderRecords();
  });
  function renderRecords() {
    manageProjects.hidden = recordGrouping !== 'project';
    updateGroupToggle();
    const scrollTop = $('record-list').scrollTop;
    $('record-list').replaceChildren(); $('record-count').textContent = records.length;
    for (const group of groupRecords(records, recordGrouping)) {
      const container = document.createElement('section'); container.className = 'record-group';
      const list = document.createElement('div'); list.className = 'record-group-items';
      const key = recordGrouping + ':' + group.key;
      list.hidden = collapsedGroups.has(key);
      const heading = actionButton('', () => {
        const opening = heading.getAttribute('aria-expanded') !== 'true';
        heading.setAttribute('aria-expanded', String(opening));
        if (opening) collapsedGroups.delete(key); else collapsedGroups.add(key);
        updateGroupToggle();
        list.hidden = !opening;
      }, 'record-group-heading');
      heading.setAttribute('aria-expanded', String(!list.hidden));
      heading.append(text('span','›','group-chevron'), text('span',group.label,'group-label'), text('span',String(group.items.length),'group-count'));
      container.append(heading, list); $('record-list').append(container);
      group.items.forEach(record => {
      const button = document.createElement('button'); button.className = 'record-item'; button.type = 'button'; button.classList.toggle('active', record.id === currentId()); button.setAttribute('aria-current', record.id === currentId() ? 'true' : 'false');
      button.title = record.title || '未命名记录';
      const mark = document.createElement('span'); mark.className = 'record-icon'; mark.append(icon('wave'));
      const copy = document.createElement('span'); copy.className = 'record-copy'; copy.append(text('span', record.title || '未命名记录', 'record-title'), text('span', recordMeta(record), 'record-meta'));
      button.append(mark, copy); button.addEventListener('click', () => selectRecord(record.id)); list.append(button);
      });
    }
    if (!records.length) $('record-list').append(text('p', '选择面试文件夹，扫描其中的录音与录屏。', 'list-placeholder'));
    $('record-list').scrollTop = scrollTop;
  }
  async function refreshLibrary() { const result = await api('/api/library'); records = result.items || []; projects = result.projects || {}; renderRecords(); }
  function guideState(settings, items, detail) {
    const target = detail?.item || items[0] || null;
    const sourceReady = settings?.source_exists === true;
    const hasTranscript = detail ? detail.result_available === true : !!target && completed(target.latest_run?.state || target.state);
    return {target, sourceReady, hasTranscript,
      canTranscribe: !!target && (detail ? detail.source_exists : target.source_exists) !== false,
      hasReview: !!detail?.review?.exists,
      primary: target ? 'record' : sourceReady ? 'scan' : 'choose'};
  }
  function showWelcome() {
    const state = guideState(boot?.settings, records, current);
    $('welcome-title').textContent = '把一次面试，变成下一次的准备';
    $('welcome-copy').textContent = 'Echo 是你的本机面试工作台：从自己的文件夹回放录音与录屏，按需转成文字、留下笔记，再搭配 Codex 复盘。';
    $('welcome-actions').hidden = false;
    $('guide-content').hidden = false;
    $('guide-primary').textContent = state.primary === 'choose' ? '选择面试文件夹 →' : state.primary === 'scan' ? '扫描面试文件夹 →' : current ? '继续本场面试 →' : '打开一场面试 →';
    $('guide-scan').disabled = !state.sourceReady || $('scan').disabled;
    $('guide-record').disabled = !state.target;
    $('guide-record').textContent = state.target && !state.canTranscribe && !current?.media_url ? '查看本场资料 →' : '打开本场回放 →';
    $('guide-codex').disabled = !state.target;
    $('guide-target').textContent = state.target ? `${current ? '当前面试' : '即将打开'}：${state.target.title || '未命名记录'}。下方“本场”操作均使用这场面试。` : state.sourceReady ? '文件夹已就绪，扫描后即可打开面试。' : '还没有选择文件夹。从第一步开始，原始媒体保留在原处。';
    $('guide-path-status').textContent = state.sourceReady ? boot.settings.source : '尚未选择可用的面试文件夹';
    $('guide-import-status').textContent = !state.target ? '扫描后从资料库选择一场面试' : state.canTranscribe || current?.media_url ? '已找到面试，打开后可直接回放' : '原媒体暂不可用，已有笔记与结果仍可查看';
    $('guide-transcribe-status').textContent = !state.target ? '打开面试后可准备试转' : state.hasTranscript ? current ? '当前版本已有逐字稿，可回听核对' : '已有转写版本，打开后核对内容' : '本场尚无可用转写，也可以先回放';
    $('guide-codex-status').textContent = state.hasReview ? '已有复盘文档，可刷新查看' : !state.target ? '打开面试后可准备交接' : state.hasTranscript ? '先核对原文，再复制本场交接' : '没有转写时，交接会先说明材料缺口';
    [state.sourceReady, !!state.target, state.hasTranscript, state.hasReview].forEach((ready, index) => {
      const element = $(['guide-path-number','guide-import-number','guide-transcribe-number','guide-codex-number'][index]);
      element.classList.toggle('done', ready); element.textContent = `0${index + 1}`;
    });
    document.querySelectorAll('[data-guide-action]').forEach(button => {
      const action = button.dataset.guideAction;
      button.disabled = ['trial','full'].includes(action) ? !state.canTranscribe : ['notes','handoff','review'].includes(action) ? !state.target : action === 'scan' ? !state.sourceReady || $('scan').disabled : false;
    });
    $('guide-progress').textContent = `${records.length} 场面试 · 从需要的一步开始`;
    $('welcome-hint').textContent = '随时从侧栏或快捷操作打开“使用指南”；当前笔记与转写版本会保留。';
    showView(view);
  }
  function openGuide() { view = 'guide'; showWelcome(); }
  async function ensureGuideRecord() {
    // Returning from the guide must preserve the selected run and unsaved draft.
    if (current) { showView('record'); return true; }
    if (records.length) return selectRecord(records[0].id);
    if (boot?.settings?.source_exists) await scan({openFirst:true});
    else openFolderSetup();
    return !!current;
  }
  async function runGuideAction(action) {
    if (action === 'choose') { openFolderSetup(); return; }
    if (action === 'paths') { openDialog('settings-dialog'); return; }
    if (action === 'queue') { showView('queue'); await refreshQueue(); return; }
    if (action === 'scan') {
      if (!boot?.settings?.source_exists) openFolderSetup();
      else await scan({openFirst:true});
      return;
    }
    if (!(await ensureGuideRecord())) return;
    if (action === 'trial' || action === 'full') {
      if (current.source_exists === false) { toast('原媒体暂不可用，请先检查文件位置。'); return; }
      openTask();
      if (action === 'trial') {
        const duration = Number(current.source_duration);
        $('task-limit').value = String(duration > 0 ? Math.min(60, duration) : 60);
      }
      return;
    }
    if (action === 'handoff') { await openHandoff(); return; }
    if (action === 'notes' || action === 'review') { toggleNotes(true); showNotesTab(action === 'notes' ? 'notes' : 'review'); }
  }
  const guideNavigation = actionButton('使用指南', openGuide, 'nav-item');
  guideNavigation.dataset.view = 'guide'; $('settings-open').before(guideNavigation);
  $('guide-primary').addEventListener('click', () => runGuideAction(guideState(boot?.settings, records, current).primary));
  $('guide-scan').addEventListener('click', scan);
  $('guide-record').addEventListener('click', () => runGuideAction('record'));
  document.querySelectorAll('[data-guide-action]').forEach(button => button.addEventListener('click', () => runGuideAction(button.dataset.guideAction)));
  let handoffMemory = null;
  function renderHandoffMemory(value) {
    handoffMemory = value || null;
    $('memory-init').hidden = !value?.root || value.initialized || !!value.error;
    $('memory-init').disabled = false;
    $('memory-init').textContent = '初始化 Memory';
    $('handoff-memory-path').textContent = value?.index_path || '';
    $('handoff-memory-status').textContent = !value
      ? '当前服务未提供 Memory 信息，更新后请重启服务。'
      : [value.error ? 'Memory 暂不可用，单场复盘仍可继续。' : value.initialized
          ? '已关联本资料库。交接将包含 Memory 入口，确认后的结论可按主题保存。'
          : value.root ? '为本资料库建立索引、个人资料模板和主题目录。初始化不会生成能力判断。' : '先选择面试文件夹，再建立本资料库的 Memory。',
        ...(value.warnings || [])].join('\n');
  }
  async function initializeMemory() {
    if (!handoffMemory?.root || handoffMemory.initialized || handoffMemory.error || $('memory-init').disabled) return;
    const generation = handoffGeneration, id = currentId(), selectedRun = runId();
    const expected = {source_directory:handoffMemory.source_directory,root:handoffMemory.root};
    $('memory-init').disabled = true; $('memory-init').textContent = '正在初始化…';
    formError('handoff-error', null);
    try {
      await api('/api/memory/init', {method:'POST',body:expected});
      if (generation !== handoffGeneration || !$('guide-dialog').open || currentId() !== id || runId() !== selectedRun) return;
      await openHandoff();
    } catch (error) {
      if (generation !== handoffGeneration || !$('guide-dialog').open) return;
      formError('handoff-error', error);
      $('memory-init').disabled = false; $('memory-init').textContent = '重试初始化';
    }
  }
  async function openHandoff() {
    const generation = ++handoffGeneration;
    handoffMemory = null; $('memory-init').hidden = true;
    $('handoff-memory-status').textContent = '正在读取 Memory 状态…';
    $('handoff-memory-path').textContent = '';
    $('guide-prompt').value = ''; $('guide-copy').disabled = true;
    $('guide-copy-status').textContent = ''; $('handoff-retry').hidden = true;
    formError('handoff-error', null); openDialog('guide-dialog');
    if (!current) { $('handoff-state').textContent = '先从资料库打开一场面试，再生成对应的复盘交接。'; return; }
    const id = currentId(), selectedRun = runId();
    $('handoff-state').textContent = '正在保存笔记并准备本场资料…';
    try {
      if (!(await flushDraft())) {
        if (generation === handoffGeneration) $('guide-dialog').close();
        return;
      }
      const query = selectedRun ? `?run=${encodeURIComponent(selectedRun)}` : '';
      const result = await api(`/api/interviews/${encodeURIComponent(id)}/handoff${query}`);
      if (generation !== handoffGeneration || !$('guide-dialog').open || currentId() !== id || runId() !== selectedRun) return;
      if (!result.prompt) throw new Error('本场资料交接为空，请刷新后重试。');
      $('guide-prompt').value = result.prompt;
      renderHandoffMemory(result.memory);
      $('handoff-state').textContent = [result.ready ? '已准备当前版本的复盘资料。' : '材料尚不完整：可先把缺口交给 Codex 确认。', ...(result.warnings || [])].join('\n');
      $('guide-copy').disabled = false;
    } catch (error) {
      if (generation !== handoffGeneration || !$('guide-dialog').open) return;
      $('handoff-state').textContent = '暂时无法生成本场交接。';
      formError('handoff-error', error); $('handoff-retry').hidden = false;
    }
  }
  $('guide-dialog').addEventListener('close', () => { handoffGeneration++; });
  document.querySelectorAll('[data-handoff]').forEach(button => button.addEventListener('click', openHandoff));
  $('guide-codex').addEventListener('click', () => runGuideAction('handoff'));
  $('codex-handoff').addEventListener('click', openHandoff);
  $('handoff-retry').addEventListener('click', openHandoff);
  $('memory-init').addEventListener('click', initializeMemory);
  $('guide-copy').addEventListener('click', async () => {
    if ($('guide-copy').disabled || !$('guide-prompt').value) return;
    try { await navigator.clipboard.writeText($('guide-prompt').value); $('guide-copy-status').textContent = '已复制，可在 Codex 项目中粘贴。'; }
    catch (_) { $('guide-prompt').focus(); $('guide-prompt').select(); $('guide-copy-status').textContent = '请复制已选中的提示词。'; }
  });
  async function selectRecord(id, requestedRun, {preserveView = false} = {}) {
    if (navigating) return false;
    navigating = true; $('run-select').disabled = true;
    try {
      if (!(await flushDraft())) return false;
      const query = requestedRun ? `?run=${encodeURIComponent(requestedRun)}` : '';
      const details = await api(`/api/interviews/${encodeURIComponent(id)}${query}`);
      // Typing can continue while the next record loads. Flush those later edits
      // before replacing the editor, as well as the edits present at the click.
      if (!(await flushDraft())) return false;
      if (currentId()) positions.set(currentId(), sourceTime());
      audio.pause(); current = details; mediaFallback = false;
      const localDraft = drafts.get(id);
      if (!localDraft || (!localDraft.dirty && Number(details.draft?.revision || 0) >= Number(localDraft.revision || 0))) drafts.set(id, newDraft(details.draft));
      activeSegment = -1; selected = -1; loopIndex = null; clearClip(); videoOpen = false; $('video-toggle').setAttribute('aria-pressed', 'false'); $('video-toggle').textContent = '展开录屏'; $('loop').setAttribute('aria-pressed', 'false');
      pendingSeek = positions.get(id) ?? offset();
      audio.removeAttribute('src');
      if (details.media_url) audio.src = details.media_url;
      $('video-toggle').hidden = true;
      audio.load(); $('find-input').value = ''; $('find-bar').hidden = true;
      renderCurrent(); setFollow(true); if (!preserveView) showView('record'); else showView(view);
      loadWaveform();
      return true;
    } catch (error) { reportError(error); return false; }
    finally { navigating = false; $('run-select').disabled = !(current?.runs?.length); if (current) $('run-select').value = runId() || ''; }
  }
  function renderCurrent() {
    if (!current) return;
    $('player-title').textContent = String(current.item.path || current.item.title || '当前录音').split(/[\\/]/).pop();
    $('player-title').title = current.item.title || '';
    $('record-title').textContent = current.item.title || '未命名记录';
    const libraryItem = records.find(item => item.id === currentId());
    $('record-meta').replaceChildren(text('span', recordMeta({...current.item, state:libraryItem?.state || current.run?.state, latest_run:current.run, duration:current.duration || current.item.duration})));
    (current.warnings || []).forEach(warning => $('record-meta').append(text('span', warning, 'warning')));
    $('segment-count').textContent = segments().length ? `${segments().length} ${current.timestamp_kind === 'audio_chunk' ? '音频块' : '段'}` : '';
    $('segment-count').title = current.timestamp_kind === 'audio_chunk' ? '时间戳对应音频块范围，并非逐句对齐。' : '模型生成的片段时间戳';
    $('notes-editor').value = draft()?.body || ''; $('notes-editor').disabled = false;
    renderRuns();
    ['loop','follow-toggle','find-toggle'].forEach(id => $(id).disabled = !segments().length);
    ['play','skip-back','skip-forward','speed','seek','jump-open','clip-open'].forEach(id => $(id).disabled = !current.media_url);
    $('new-task').disabled = current.source_exists === false;
    $('new-task').title = current.source_exists === false ? '原始媒体已不在原路径，无法创建新转写。' : '创建转写任务';
    renderReview();
    $('legacy-notes').replaceChildren();
    (current.notes || []).forEach(note => $('legacy-notes').append(text('p', [note.at, note.body].filter(Boolean).join('\n'), 'legacy-note')));
    $('legacy-notes-section').hidden = !current.notes?.length;
    renderRecords(); renderSegments(); renderCitations(); updateSaveState(); updateTime(); updatePlayState();
  }
  function renderReview() {
    const review = current?.review || {};
    $('review-body').textContent = [...(review.warnings || []), review.exists ? review.body || '这份复盘文档为空。' : '还没有复盘文档。点击“交给 Codex 复盘”，完成后回到这里刷新。'].join('\n\n');
    $('review-path').textContent = review.path || '';
    $('review-status').textContent = review.exists ? review.status || '已有复盘' : '待复盘';
    $('review-indicator').hidden = !review.exists;
  }
  async function refreshReview() {
    if (!current) return;
    const id = currentId(), selectedRun = runId();
    $('review-refresh').disabled = true;
    try {
      const query = selectedRun ? `?run=${encodeURIComponent(selectedRun)}` : '';
      const details = await api(`/api/interviews/${encodeURIComponent(id)}${query}`);
      if (currentId() !== id || runId() !== selectedRun) return;
      current.review = details.review; renderReview(); toast(details.review?.exists ? '复盘文档已刷新' : '尚未找到本场复盘文档');
    } catch (error) { reportError(error); }
    finally { $('review-refresh').disabled = false; }
  }
  $('review-refresh').addEventListener('click', refreshReview);
  $('run-select').addEventListener('change', event => selectRecord(currentId(), event.target.value));
  async function scan({openFirst = false} = {}) {
    if ($('scan').disabled) return;
    $('scan').disabled = true; $('guide-scan').disabled = true; $('guide-primary').disabled = true;
    try { const result = await api('/api/scan', {method:'POST', body:{}}); await refreshLibrary(); showWelcome(); if (openFirst && records.length && !current) await selectRecord(records[0].id); toast(result.message || `扫描完成，文件夹中共 ${records.length} 份记录`); }
    catch (error) { reportError(error); }
    finally {
      $('scan').disabled = false; $('guide-primary').disabled = false; $('guide-scan').disabled = !boot?.settings?.source_exists;
      document.querySelectorAll('[data-guide-action="scan"]').forEach(button => { button.disabled = !boot?.settings?.source_exists; });
    }
  }
  $('scan').addEventListener('click', scan);

  function renderSegments() {
    $('segments').replaceChildren(); $('transcript-end').hidden = !segments().length;
    if (!segments().length) {
      const empty = document.createElement('div'); empty.className = 'transcript-empty';
      empty.append(icon('wave'), text('h2', current.run ? '这次转写没有可显示的段落' : current.media_url ? '面试已就绪，可以直接回放' : '原媒体暂不可用'), text('p', current.run ? '可切换其他版本，或调整范围重新转写。' : current.media_url ? '使用下方播放器回听。需要逐字稿时，再把语音转成文字。' : '请检查原始文件是否仍在原路径。已有笔记和复盘文档仍然保留。'));
      if (current.media_url) empty.append(actionButton('播放面试', playPause, 'primary empty-action'));
      if (current.source_exists !== false) empty.append(actionButton('语音转文字', openTask, 'quiet-button empty-action'));
      $('segments').append(empty); return;
    }
    const fragment = document.createDocumentFragment();
    segments().forEach((segment, index) => {
      const article = document.createElement('article'); article.className = 'segment'; article.dataset.index = index;
      article.tabIndex = 0; article.setAttribute('aria-label', `第 ${index + 1} 段，${time(segment.start)}，按回车定位回听`);
      article.addEventListener('click', event => {
        if (event.target.closest('button,a,input') || window.getSelection()?.toString()) return;
        selectSegment(index);
      });
      article.addEventListener('keydown', event => {
        if (event.target === article && event.key === 'Enter') { event.preventDefault(); selectSegment(index); }
      });
      const head = document.createElement('div'); head.className = 'segment-head';
      const stamp = actionButton(time(segment.start), () => selectSegment(index), 'timestamp'); stamp.setAttribute('aria-label', '回听 ' + time(segment.start)); stamp.disabled = !current.media_url || Number(segment.start) < offset();
      const speaker = text('span', segment.speaker || segment.speaker_label || '', 'speaker');
      const actions = document.createElement('div'); actions.className = 'segment-actions';
      const add = actionButton('', () => addCitation(index), 'add-reference icon-button'); add.append(icon('plus')); add.title = '引用这段原文到笔记'; add.setAttribute('aria-label', `引用第 ${index + 1} 段到笔记`);
      actions.append(add); head.append(stamp, speaker, actions); article.append(head, text('p', segment.text || '', 'segment-text')); fragment.append(article);
    });
    $('segments').append(fragment); refreshMatches(); updateSegmentHighlight();
  }
  function setFollow(on) { following = on; $('follow-toggle').setAttribute('aria-pressed', String(on)); $('resume-follow').hidden = on || !segments().length; }
  function scrollToSegment(index, immediate = false) {
    const element = $('segments').children[index]; if (!element) return;
    const scroller = $('transcript-scroll'); const top = element.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
    scroller.scrollTo({top:Math.max(0, top - 35), behavior:'instant'});
  }
  function selectSegment(index) {
    const segment = segments()[index]; if (!segment || !current?.media_url || Number(segment.start) < offset()) return;
    selected = index; if (loopIndex !== null) loopIndex = index;
    setFollow(true); seek(segment.start); scrollToSegment(index); updateSegmentHighlight();
  }
  function addCitation(index) {
    const segment = segments()[index], state = draft(); if (!segment || !state) return;
    showNotesTab('notes');
    if (state.citations.some(citation => citation.run_id === runId() && citation.index === index)) { toast('这段原文已经在笔记里'); toggleNotes(true); return; }
    state.citations.push({run_id:runId(), index, start:segment.start, end:segment.end, text:segment.text || ''});
    selected = index; renderCitations(); updateSegmentHighlight(); toggleNotes(true); markDraftDirty(); toast('已加入引用，点击时间点可回听');
  }
  function renderCitations() {
    $('citations').replaceChildren(); const citations = draft()?.citations || []; $('citation-count').textContent = citations.length;
    citations.forEach((citation, index) => {
      const card = document.createElement('div'); card.className = 'citation';
      const footer = document.createElement('div'); footer.className = 'citation-footer';
      const link = actionButton('', async () => {
        if (citation.run_id && citation.run_id !== runId()) { if (!(await selectRecord(currentId(), citation.run_id))) return; }
        if (matchMedia('(max-width:1050px)').matches) toggleNotes(false);
        if (segments()[citation.index] && Math.abs(Number(segments()[citation.index].start) - Number(citation.start)) < 0.2) selectSegment(citation.index);
        else seek(citation.start);
      }, 'citation-link'); link.append(icon('arrow'), document.createTextNode(time(citation.start))); link.setAttribute('aria-label', '跳回引用 ' + time(citation.start));
      if (citation.run_id !== runId()) link.append(text('span', '其他版本', 'citation-run'));
      const remove = actionButton('', () => { draft().citations.splice(index, 1); renderCitations(); markDraftDirty(); }, 'icon-button remove-citation'); remove.append(icon('close')); remove.setAttribute('aria-label', '移除引用 ' + time(citation.start));
      footer.append(link, remove); card.append(text('p', citation.text, 'citation-quote'), footer); $('citations').append(card);
    });
  }
  function releaseFollow() { if (segments().length) setFollow(false); }
  $('transcript-scroll').addEventListener('wheel', releaseFollow, {passive:true}); $('transcript-scroll').addEventListener('touchmove', releaseFollow, {passive:true});
  $('transcript-scroll').addEventListener('pointerdown', event => { if (event.target === $('transcript-scroll')) releaseFollow(); });
  $('transcript-scroll').addEventListener('keydown', event => { if (['ArrowUp','ArrowDown','PageUp','PageDown','Home','End'].includes(event.key) && !escapeEditable(event.target)) releaseFollow(); });
  $('follow-toggle').addEventListener('click', () => { setFollow(!following); if (following) scrollToSegment(Math.max(0, activeSegment)); });
  $('resume-follow').addEventListener('click', () => { setFollow(true); scrollToSegment(Math.max(0, activeSegment)); });
  function updateSegmentHighlight() { Array.from($('segments').children).forEach((element, index) => { element.classList.toggle('playing', index === activeSegment); element.classList.toggle('selected', index === selected); }); }
  function updateTime() {
    const duration = Number.isFinite(audio.duration) ? audio.duration + offset() : Number(current?.duration) || 0;
    $('current-time').textContent = time(sourceTime()); $('total-time').textContent = duration ? time(duration) : '--:--';
    $('seek').min = offset(); $('seek').max = duration || Math.max(offset(), 100); $('seek').value = sourceTime();
    $('seek').style.setProperty('--progress', duration > offset() ? `${(sourceTime() - offset()) / (duration - offset()) * 100}%` : '0%');
    const span = Math.max(.001, duration - offset());
    const progress = Math.max(0, Math.min(512, (sourceTime() - offset()) / span * 512));
    $('wave-progress-clip').setAttribute('width', String(progress));
    $('wave-cursor').setAttribute('x1', String(progress)); $('wave-cursor').setAttribute('x2', String(progress));
    $('wave-start').textContent = time(offset()); $('wave-middle').textContent = time(offset() + span / 2); $('wave-end').textContent = duration ? time(duration) : '--:--';
    $('seek').setAttribute('aria-valuetext', time(sourceTime()) + ' / ' + time(duration));
    $('wave-selection').setAttribute('x', clipLoop ? String((clipLoop.start - offset()) / span * 512) : '0');
    $('wave-selection').setAttribute('width', clipLoop ? String((clipLoop.end - clipLoop.start) / span * 512) : '0');
    const next = segments().findIndex(segment => sourceTime() >= Number(segment.start) && sourceTime() < Number(segment.end));
    if (next !== activeSegment) { activeSegment = next; updateSegmentHighlight(); if (following && next >= 0 && !audio.paused) scrollToSegment(next); }
    if (loopIndex !== null && !audio.paused) { const segment = segments()[loopIndex]; if (segment && sourceTime() >= Number(segment.end)) seek(segment.start); }
    if (clipLoop && !audio.paused && sourceTime() >= clipLoop.end) seek(clipLoop.start);
  }
  function seek(value) {
    if (!current?.media_url) { toast('这份记录暂时没有可播放的媒体'); return; }
    if (Number(value) < offset()) { toast('当前仅有部分转写音频，这个时间点不可回听'); return; }
    if (clipLoop && (Number(value) < clipLoop.start || Number(value) > clipLoop.end)) clearClip();
    const duration = Number.isFinite(audio.duration) ? audio.duration : Math.max(0, Number(current.duration) - offset());
    const target = Math.max(0, Math.min(duration || Number.MAX_SAFE_INTEGER, Number(value) - offset()));
    if (audio.readyState < 1) pendingSeek = target + offset(); else audio.currentTime = target;
    updateTime();
  }
  async function playPause() {
    if (!current?.media_url) return;
    if (!audio.paused) { audio.pause(); return; }
    if (audio.ended) seek(clipLoop ? clipLoop.start : loopIndex !== null ? segments()[loopIndex].start : offset());
    try { await audio.play(); }
    catch (_) { $('media-state').textContent = '播放失败'; toast('当前媒体无法播放，请检查文件是否存在及媒体格式。'); }
  }
  function updatePlayState() {
    $('player').classList.toggle('is-playing', !audio.paused && !audio.ended && !audio.error);
    $('play-icon').setAttribute('href', audio.paused ? '#i-play' : '#i-pause'); $('play').setAttribute('aria-label', audio.paused ? '播放' : '暂停');
    $('media-state').textContent = !current?.media_url ? '媒体不可用' : audio.error ? '无法播放此格式' : audio.paused ? (audio.ended ? '播放结束' : offset() ? '转写片段音频' : '本机媒体') : '正在播放';
  }
  audio.addEventListener('loadedmetadata', () => { $('video-toggle').hidden = !(audio.videoWidth > 0); audio.playbackRate = Number($('speed').value); if (pendingSeek !== null) { const target = pendingSeek; pendingSeek = null; seek(target); } updateTime(); updatePlayState(); });
  audio.addEventListener('timeupdate', updateTime); audio.addEventListener('play', updatePlayState); audio.addEventListener('pause', updatePlayState);
  audio.addEventListener('ended', () => { if (clipLoop) { seek(clipLoop.start); playPause(); } else if (loopIndex !== null && segments()[loopIndex]) { seek(segments()[loopIndex].start); playPause(); } else updatePlayState(); });
  audio.addEventListener('error', () => {
    if (current?.cached_media_url && !mediaFallback && current.media_url !== current.cached_media_url) {
      const position = sourceTime(); mediaFallback = true; current.media_offset = Number(current.cached_media_offset) || 0; current.media_url = current.cached_media_url;
      pendingSeek = Math.max(offset(), position); clearClip(); audio.src = current.media_url; audio.load(); loadWaveform(); toast('已切换为转写时保存的回听音频'); return;
    }
    updatePlayState(); if (current?.media_url) toast('当前媒体格式无法播放，或文件已不可用。');
  });
  $('play').addEventListener('click', playPause); $('skip-back').addEventListener('click', () => seek(Math.max(offset(), sourceTime() - 10))); $('skip-forward').addEventListener('click', () => seek(sourceTime() + 10));
  function scrubTo(value) {
    loopIndex = null; $('loop').setAttribute('aria-pressed','false');
    setFollow(true); seek(value);
    const target = Number(value);
    let index = segments().findIndex(segment => target >= Number(segment.start) && target < Number(segment.end));
    // In silence, show the nearest transcript paragraph without changing the requested audio time.
    if (index < 0 && segments().length) {
      const distance = segment => Math.max(Number(segment.start) - target, target - Number(segment.end), 0);
      index = segments().reduce((best, segment, i, all) => distance(segment) < distance(all[best]) ? i : best, 0);
    }
    selected = index; updateSegmentHighlight();
    if (index >= 0) scrollToSegment(index);
  }
  $('seek').addEventListener('input', event => scrubTo(event.target.value));
  $('speed').addEventListener('change', event => audio.playbackRate = Number(event.target.value));
  $('loop').addEventListener('click', () => { if (!segments().length) return; clearClip(); loopIndex = loopIndex === null ? Math.max(0, selected >= 0 ? selected : activeSegment) : null; $('loop').setAttribute('aria-pressed', String(loopIndex !== null)); if (loopIndex !== null) { seek(segments()[loopIndex].start); toast('循环当前段落，再点一次取消'); } });
  function mediaEnd() { return Number.isFinite(audio.duration) ? audio.duration + offset() : Number(current?.duration) || 0; }
  function parsePlaybackTime(value) {
    const parts = String(value).trim().split(':');
    if (!parts.length || parts.length > 3 || parts.some(part => !/^\d+(?:\.\d+)?$/.test(part))) throw new Error('请输入秒数、分:秒或时:分:秒。');
    if (parts.slice(1).some(part => Number(part) >= 60)) throw new Error('冒号后的分、秒必须小于 60。');
    if (parts.slice(0, -1).some(part => !/^\d+$/.test(part))) throw new Error('只有秒数可以包含小数。');
    const seconds = parts.reduce((total, part) => total * 60 + Number(part), 0);
    if (!Number.isFinite(seconds)) throw new Error('时间超出可用范围。');
    return seconds;
  }
  function clearClip() {
    clipLoop = null; $('clip-open').textContent = 'A–B 片段回听'; $('clip-open').setAttribute('aria-pressed', 'false'); $('clip-stop').hidden = true; $('wave-selection').setAttribute('width', '0');
  }
  $('clip-stop').addEventListener('click', clearClip);
  $('jump-open').addEventListener('click', () => { $('jump-time').value = time(sourceTime()); formError('jump-error', null); openDialog('jump-dialog'); $('jump-time').focus(); $('jump-time').select(); });
  $('jump-form').addEventListener('submit', event => {
    event.preventDefault();
    try { const target = parsePlaybackTime($('jump-time').value); if (target < offset() || target > mediaEnd()) throw new Error(`可跳转范围：${time(offset())}–${time(mediaEnd())}`); seek(target); $('jump-dialog').close(); }
    catch (error) { formError('jump-error', error); }
  });
  $('clip-open').addEventListener('click', () => {
    $('clip-start').value = time(clipLoop?.start ?? sourceTime()); $('clip-end').value = time(clipLoop?.end ?? Math.min(mediaEnd(), sourceTime() + 20));
    formError('clip-error', null); openDialog('clip-dialog');
  });
  $('clip-start-now').addEventListener('click', () => $('clip-start').value = time(sourceTime()));
  $('clip-end-now').addEventListener('click', () => $('clip-end').value = time(sourceTime()));
  $('clip-form').addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const start = parsePlaybackTime($('clip-start').value), end = parsePlaybackTime($('clip-end').value);
      if (start < offset() || end > mediaEnd() || end - start < .5) throw new Error(`请在 ${time(offset())}–${time(mediaEnd())} 内选择至少半秒的片段，终点应晚于起点。`);
      clipLoop = {start, end}; loopIndex = null; $('loop').setAttribute('aria-pressed', 'false');
      $('clip-open').textContent = `A ${time(start)} — B ${time(end)}`; $('clip-open').setAttribute('aria-pressed', 'true'); $('clip-stop').hidden = false;
      seek(start); $('clip-dialog').close(); if (audio.paused) await playPause();
    } catch (error) { formError('clip-error', error); }
  });
  $('video-toggle').addEventListener('click', () => { videoOpen = !videoOpen; $('video-stage').hidden = !videoOpen; $('video-toggle').setAttribute('aria-pressed', String(videoOpen)); $('video-toggle').textContent = videoOpen ? '收起录屏' : '展开录屏'; });
  $('video-close').addEventListener('click', () => { videoOpen = false; $('video-stage').hidden = true; $('video-toggle').setAttribute('aria-pressed', 'false'); $('video-toggle').textContent = '展开录屏'; });
  async function loadWaveform() {
    waveformController?.abort(); const controller = new AbortController(); waveformController = controller; const generation = ++waveformGeneration;
    $('wave-background').removeAttribute('d'); $('wave-played').removeAttribute('d'); $('wave-retry').hidden = true;
    if (!current?.media_url) { $('wave-status').textContent = '没有可播放的媒体'; return; }
    $('wave-status').textContent = '正在读取真实音频波形…';
    const query = new URLSearchParams(); if (runId()) query.set('run', runId()); if (mediaFallback) query.set('cached', '1');
    try {
      const result = await api(`/api/interviews/${encodeURIComponent(currentId())}/waveform?${query}`, {signal:controller.signal, timeoutMs:90000});
      if (generation !== waveformGeneration) return;
      const peaks = result.peaks;
      if (!Array.isArray(peaks) || !peaks.length || peaks.length > 512 || peaks.some(value => !Number.isFinite(value) || value < 0 || value > 1)) throw new Error('波形数据不可用，仍可使用时间轴播放。');
      const maximum = Math.max(...peaks, .001), step = 512 / peaks.length;
      const upper = peaks.map((peak,index) => `${index * step},${24 - peak / maximum * 21}`);
      const lower = peaks.map((peak,index) => `${index * step},${24 + peak / maximum * 21}`).reverse();
      const shape = `M0,24 L${upper.join(' L')} L512,24 L${lower.join(' L')} Z`;
      $('wave-background').setAttribute('d', shape); $('wave-played').setAttribute('d', shape);
      $('wave-status').textContent = maximum > .001 ? '点击波形定位 · 拖动精听' : '音轨接近静音'; updateTime();
    } catch (error) { if (generation === waveformGeneration && !controller.signal.aborted) { $('wave-status').textContent = error.message; $('wave-retry').hidden = false; } }
  }
  $('wave-retry').addEventListener('click', loadWaveform);
  $('wave-track').addEventListener('pointermove', event => {
    const rect = $('wave-track').getBoundingClientRect(), ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    $('wave-hover').textContent = time(offset() + ratio * Math.max(0, mediaEnd() - offset()));
    $('wave-hover').style.left = Math.max(26, Math.min(rect.width - 26, ratio * rect.width)) + 'px'; $('wave-hover').hidden = false;
  });
  $('wave-track').addEventListener('pointerleave', () => $('wave-hover').hidden = true);
  function openFind() { if (!segments().length) return; showView('record'); $('find-bar').hidden = false; $('find-input').focus(); refreshMatches(); }
  function closeFind() { $('find-bar').hidden = true; $('find-input').value = ''; refreshMatches(); $('find-toggle').focus(); }
  function refreshMatches() {
    matches = []; matchCursor = -1; const query = $('find-input').value.trim().toLocaleLowerCase();
    segments().forEach((segment, index) => {
      const paragraph = $('segments').children[index]?.querySelector('.segment-text'); if (!paragraph) return;
      const source = segment.text || ''; paragraph.replaceChildren(); let cursor = 0, found;
      if (query) while ((found = source.toLocaleLowerCase().indexOf(query, cursor)) !== -1) {
        paragraph.append(document.createTextNode(source.slice(cursor, found))); const mark = text('mark', source.slice(found, found + query.length)); paragraph.append(mark); matches.push({index, mark}); cursor = found + query.length;
      }
      paragraph.append(document.createTextNode(source.slice(cursor)));
    });
    $('find-count').textContent = query ? `${matches.length} 处匹配` : ''; $('find-next').disabled = !matches.length;
  }
  function nextMatch(step = 1) { if (!matches.length) return; matchCursor = (matchCursor + step + matches.length) % matches.length; matches.forEach((match, index) => match.mark.classList.toggle('current-match', index === matchCursor)); setFollow(false); scrollToSegment(matches[matchCursor].index); $('find-count').textContent = `${matchCursor + 1} / ${matches.length}`; }
  $('find-toggle').addEventListener('click', openFind); $('find-close').addEventListener('click', closeFind); $('find-input').addEventListener('input', refreshMatches); $('find-next').addEventListener('click', () => nextMatch());
  $('find-input').addEventListener('keydown', event => { if (event.key === 'Enter') { event.preventDefault(); nextMatch(event.shiftKey ? -1 : 1); } if (event.key === 'Escape') { event.preventDefault(); closeFind(); } });

  function renderCommands() {
    const options = [...records.map(record => ({title:record.title || '未命名记录', hint:'打开记录', action:() => selectRecord(record.id)})),
      {title:'选择面试文件夹', hint:'⌘O', action:openFolderSetup}, {title:'添加单个录音或录屏', hint:'导入', action:() => openDialog('import-dialog')}, {title:'扫描面试文件夹', hint:'资料库', action:scan},
      {title:'交给 Codex 复盘', hint:'当前面试', action:openHandoff},
      {title:'使用指南 · Intro 与场景 Cookbook', hint:'帮助', action:openGuide},
      {title:'打开处理队列', hint:'转写', action:() => showView('queue')}, {title:'查找逐字稿', hint:'⌘F', action:openFind},
      {title:'显示 / 隐藏复盘', hint:'工作区', action:() => toggleNotes()}, ...themeCommands(),
      {title:'设置与路径', hint:'偏好', action:() => openDialog('settings-dialog')}];
    const query = $('command-input').value.trim().toLocaleLowerCase(); commands = options.filter(option => (option.title + option.hint).toLocaleLowerCase().includes(query)); commandCursor = 0;
    $('command-results').replaceChildren();
    commands.forEach((command, index) => { const button = actionButton('', () => runCommand(index), 'command-item'); button.classList.toggle('active', index === 0); button.append(text('span', command.title), text('small', command.hint)); $('command-results').append(button); });
    if (!commands.length) $('command-results').append(text('p', '没有匹配的记录或操作', 'helper'));
  }
  function runCommand(index) { const command = commands[index]; if (!command) return; $('command-dialog').close(); command.action(); }
  $('command-input').addEventListener('input', renderCommands);
  $('command-input').addEventListener('keydown', event => {
    if (['ArrowDown','ArrowUp'].includes(event.key) && commands.length) { event.preventDefault(); commandCursor = (commandCursor + (event.key === 'ArrowDown' ? 1 : -1) + commands.length) % commands.length; Array.from($('command-results').children).forEach((element,index) => element.classList.toggle('active', index === commandCursor)); $('command-results').children[commandCursor].scrollIntoView({block:'nearest'}); }
    if (event.key === 'Enter') { event.preventDefault(); runCommand(commandCursor); }
  });
  document.addEventListener('keydown', event => {
    const mod = event.metaKey || event.ctrlKey, key = event.key.toLowerCase(), modal = document.querySelector('dialog[open]');
    if (mod && key === 'k' && !modal) { event.preventDefault(); openDialog('command-dialog'); return; }
    if (modal) return;
    if (mod && key === 'o') { event.preventDefault(); openFolderSetup(); }
    if (mod && key === 'f' && view === 'record' && current) { event.preventDefault(); openFind(); }
    if (mod && key === 's' && current) { event.preventDefault(); flushDraft(); }
    if (event.code === 'Space' && !mod && !event.altKey && !escapeEditable(event.target) && !event.target.closest('button') && view === 'record') { event.preventDefault(); playPause(); }
    if (event.key === 'Escape') {
      const menu = $('record-more');
      if (menu.open) { event.preventDefault(); menu.open = false; menu.querySelector('summary').focus(); return; }
      if ($('main').classList.contains('notes-mobile-open')) toggleNotes(false);
      $('shell').classList.remove('sidebar-mobile-open');
    }
  });
  $('import-hint').textContent = '粘贴面试文件夹内音频或视频的完整路径，每行一个。原文件保留在原处，转写由你启动。';
  $('import-form').addEventListener('submit', async event => {
    event.preventDefault(); const paths = $('import-paths').value.split(/\r?\n/).map(path => path.trim()).filter(Boolean); if (!paths.length) return;
    $('import-submit').disabled = true; formError('import-error', null);
    try { const result = await api('/api/import', {method:'POST', body:{paths}}); await refreshLibrary(); $('import-dialog').close(); $('import-paths').value = ''; if (result.selected_id) await selectRecord(result.selected_id); else showWelcome(); toast(`已添加 ${result.items?.length || paths.length} 份媒体`); }
    catch (error) { formError('import-error', error); }
    finally { $('import-submit').disabled = false; }
  });
  const layoutField = document.createElement('label'); layoutField.className = 'field';
  layoutField.innerHTML = '结果目录结构<select id="output-layout"><option value="mirror">保持原文件夹层级</option><option value="company">按公司 / 原面试文件夹归集</option></select><small>公司由原面试文件夹名称识别。修改设置只影响新结果；已有资料需单独整理。</small>';
  $('output-path').closest('label').after(layoutField);
  function updateOutputMode() {
    const automatic = $('default-output').checked;
    $('output-path').disabled = automatic;
    document.querySelector('[data-pick-dir="output-path"]').disabled = automatic;
    if (automatic) $('output-path').value = $('source-path').value ? $('source-path').value.replace(/\/+$/, '') + '/echo' : '';
  }
  $('default-output').addEventListener('change', updateOutputMode);
  $('source-path').addEventListener('input', updateOutputMode);
  let storageLocationGeneration = 0;
  async function refreshStorageLocations() {
    const generation = ++storageLocationGeneration, id = currentId(), selectedRun = runId();
    const source = boot?.settings?.source || '';
    $('settings-review-record').textContent = current?.item?.title || '从资料库选择一场面试，可查看对应复盘文件。';
    $('settings-review-path').textContent = '';
    $('settings-review-status').textContent = id ? '正在读取本场复盘位置…' : '每场面试有各自的复盘文件，由 Codex 保存后在 Echo 中刷新查看。';
    $('settings-memory-path').textContent = '';
    $('settings-memory-status').textContent = '正在读取 Memory 位置…';
    const query = selectedRun ? `?run=${encodeURIComponent(selectedRun)}` : '';
    const [memoryResult, recordResult] = await Promise.allSettled([
      api('/api/memory'), id ? api(`/api/interviews/${encodeURIComponent(id)}${query}`) : Promise.resolve(null),
    ]);
    if (generation !== storageLocationGeneration || currentId() !== id || runId() !== selectedRun || (boot?.settings?.source || '') !== source) return;
    if (recordResult.status === 'fulfilled' && recordResult.value?.review) {
      const review = recordResult.value.review;
      $('settings-review-path').textContent = review.path || '';
      $('settings-review-status').textContent = review.exists ? '已有复盘记录；继续讨论时追加到这份文件。' : '尚未创建；将交接文字发送给 Codex 后，复盘写入此位置。';
    } else if (recordResult.status === 'rejected') {
      $('settings-review-status').textContent = '暂时无法读取本场复盘位置，请关闭后重新打开设置。';
    }
    if (memoryResult.status === 'fulfilled') {
      const memory = memoryResult.value;
      $('settings-memory-path').textContent = memory.root || '';
      $('settings-memory-status').textContent = memory.initialized ? '已绑定本资料库。修改转写输出位置后仍沿用此目录。' : memory.root ? '尚未初始化。可在“交给 Codex 复盘”中建立 Memory。' : '选择并保存面试文件夹后，可建立资料库 Memory。';
    } else {
      $('settings-memory-status').textContent = memoryResult.reason?.message || '暂时无法读取 Memory 位置。';
    }
  }
  function populateSettings() {
    const settings = boot?.settings || {};
    $('output-layout').value = settings.output_layout || 'mirror';
    $('source-path').value = settings.source || ''; $('output-path').value = settings.output || '';
    $('default-output').checked = !settings.source || settings.output_mode === 'default'; updateOutputMode();
    $('output-settings').open = true;
    refreshStorageLocations();
    $('source-status').textContent = !settings.source ? '尚未选择。请选择已有的面试文件夹。' : settings.source_exists === false ? '目录尚不存在，请选择已有的面试文件夹。' : '';
    $('output-status').textContent = settings.output_exists === false ? '保存路径时会按服务规则检查或创建目录。' : '';
    formError('settings-error', null);
  }
  let directoryTarget = null, directoryPath = null, directoryParent = null, directoryGeneration = 0;
  async function browseDirectory(path, initial = false) {
    const generation = ++directoryGeneration;
    directoryPath = null; directoryParent = path?.replace(/\/+$/, '').replace(/\/[^/]*$/, '') || '/';
    $('directory-location').value = path || '';
    $('directory-confirm').disabled = true; $('directory-parent').disabled = !path || path === '/';
    $('directory-list').replaceChildren(); $('directory-list').setAttribute('aria-busy', 'true');
    $('directory-status').textContent = '正在读取文件夹…'; formError('directory-error', null);
    try {
      const result = await api('/api/directories', {method:'POST', body:{path, initial, hidden:$('directory-hidden').checked}});
      if (generation !== directoryGeneration || !$('directory-dialog').open) return;
      directoryPath = result.path; directoryParent = result.parent;
      $('directory-location').value = result.path; $('directory-parent').disabled = !result.parent;
      for (const folder of result.directories) {
        const button = actionButton('', () => browseDirectory(folder.path));
        button.setAttribute('aria-label', '打开文件夹 ' + folder.name);
        button.append(text('span', folder.name), text('span', '›'));
        $('directory-list').append(button);
      }
      $('directory-status').textContent = (result.notice ? result.notice + ' ' : '') + (result.directories.length ? `${result.directories.length} 个子文件夹 · 点击进入，或选择当前文件夹` : '没有可见的子文件夹，可以直接选择当前文件夹。');
      $('directory-confirm').disabled = false;
    } catch (error) {
      if (generation !== directoryGeneration || !$('directory-dialog').open) return;
      formError('directory-error', error); $('directory-status').textContent = '';
    } finally { if (generation === directoryGeneration) $('directory-list').setAttribute('aria-busy', 'false'); }
  }
  $('directory-dialog').addEventListener('close', () => { directoryGeneration++; directoryPath = null; });
  $('directory-location').addEventListener('input', () => { directoryGeneration++; directoryPath = null; $('directory-confirm').disabled = true; $('directory-list').setAttribute('aria-busy', 'false'); $('directory-status').textContent = '按回车或点击前往，打开这个位置。'; });
  $('directory-location-form').addEventListener('submit', event => { event.preventDefault(); browseDirectory($('directory-location').value.trim()); });
  $('directory-parent').addEventListener('click', () => browseDirectory(directoryParent));
  $('directory-home').addEventListener('click', () => browseDirectory(''));
  $('directory-hidden').addEventListener('change', () => browseDirectory($('directory-location').value.trim()));
  $('directory-confirm').addEventListener('click', () => {
    if (!directoryPath || !directoryTarget) return;
    $(directoryTarget).value = directoryPath; updateOutputMode();
    $(directoryTarget === 'source-path' ? 'source-status' : 'output-status').textContent = '已选择，保存路径后生效。';
    $('directory-dialog').close(); $(directoryTarget).focus();
  });
  async function pickDirectory(target) {
    directoryTarget = target;
    $('directory-heading').textContent = target === 'source-path' ? '选择面试文件夹' : '选择输出文件夹';
    openDialog('directory-dialog'); browseDirectory($(target).value.trim(), true);
  }
  function openFolderSetup() {
    openDialog('settings-dialog'); pickDirectory('source-path');
  }
  document.querySelectorAll('[data-pick-dir]').forEach(button => {
    button.textContent = '选择文件夹';
    button.setAttribute('aria-label', button.dataset.pickDir === 'source-path' ? '选择面试文件夹' : '选择输出文件夹');
    button.addEventListener('click', () => pickDirectory(button.dataset.pickDir));
  });
  function clearCurrentRecord() {
    if (currentId()) positions.set(currentId(), sourceTime());
    audio.pause(); audio.removeAttribute('src'); audio.load();
    waveformController?.abort(); waveformGeneration++; handoffGeneration++;
    current = null; videoOpen = false; pendingSeek = null; selected = -1; activeSegment = -1; loopIndex = null; clipLoop = null;
    records = []; renderRecords(); showView('guide');
  }
  async function saveSettings() {
    $('settings-submit').disabled = true; formError('settings-error', null);
    try {
      if (navigating) throw new Error('正在打开面试，请稍后再保存文件夹。');
      if ($('scan').disabled) throw new Error('文件夹仍在扫描，请扫描完成后再保存。');
      if (!(await flushDraft())) { $('settings-dialog').close(); return; }
      const previousSource = boot?.settings?.source, previousOutput = boot?.settings?.output;
      const result = await api('/api/settings', {method:'POST', body:{source:$('source-path').value.trim(), output:$('output-path').value.trim(), output_mode:$('default-output').checked ? 'default' : 'custom', output_layout:$('output-layout').value}});
      boot.settings = result.settings || result;
      // Clear the old scope immediately after saving. A failed refresh or scan
      // must never leave a previous folder's record under the new folder name.
      if (boot.settings.source !== previousSource) clearCurrentRecord();
      updateWorkspaceLabel(); populateSettings(); await refreshLibrary();
      if (current && boot.settings.output !== previousOutput) await refreshReview();
      $('settings-dialog').close(); showWelcome(); await scan({openFirst:true});
    }
    catch (error) { formError('settings-error', error); }
    finally { $('settings-submit').disabled = false; }
  }
  $('settings-form').addEventListener('submit', event => { event.preventDefault(); saveSettings(); });

  function modelStatus() {
    const model = boot?.models?.find(item => item.id === $('task-model').value), runtime = boot?.runtime;
    const missing = [];
    if (model && runtime?.backends?.[model.backend] === false) missing.push('当前运行环境尚未安装此模型所需依赖');
    if (runtime && runtime.ffmpeg === false) missing.push('当前运行环境未找到 FFmpeg');
    if (runtime && runtime.ffprobe === false) missing.push('当前运行环境未找到 FFprobe');
    const hint = [model?.description, model?.timing, model?.ready ? '模型已下载' : '首次转写会下载模型文件', ...missing].filter(Boolean).join('。');
    $('model-hint').textContent = hint;
    $('task-submit').disabled = !model || missing.length > 0;
  }
  function openTask() {
    if (!current || current.source_exists === false) return;
    $('task-record').textContent = current.item.title;
    $('task-model').replaceChildren(); (boot?.models || []).forEach(model => { const option = text('option', model.name || model.id); option.value = model.id; $('task-model').append(option); });
    $('task-model').value = boot?.default_model || boot?.models?.[0]?.id || '';
    $('task-language').replaceChildren(); Object.entries(boot?.languages || {zh:'中文'}).forEach(([value,label]) => { const option = text('option', label); option.value = value; $('task-language').append(option); });
    $('task-language').value = 'zh'; $('task-start').value = '0'; $('task-limit').value = ''; $('task-prompt').value = boot?.default_prompt || ''; $('task-force').checked = false; $('task-chunk').value = '25';
    modelStatus(); formError('task-error', null); openDialog('task-dialog');
  }
  $('new-task').addEventListener('click', openTask); $('task-model').addEventListener('change', modelStatus);
  $('task-form').addEventListener('submit', async event => {
    event.preventDefault(); if (!current) return; $('task-submit').disabled = true; formError('task-error', null);
    const options = {model:$('task-model').value, language:$('task-language').value, start:Number($('task-start').value), limit:$('task-limit').value ? Number($('task-limit').value) : 0, prompt:$('task-prompt').value, force:$('task-force').checked, chunk_seconds:Number($('task-chunk').value), silence_filter:$('task-silence').checked};
    try { await api('/api/queue', {method:'POST', body:{interview_id:currentId(), options}}); $('task-dialog').close(); showView('queue'); await refreshQueue(); toast('转写任务已加入队列'); }
    catch (error) { formError('task-error', error); }
    finally { modelStatus(); }
  });
  function renderJobs(workerActive) {
    const key = JSON.stringify([workerActive, jobs]);
    $('queue-count').textContent = jobs.filter(job => ['queued','running'].includes(job.state)).length || '';
    $('worker-state').textContent = workerActive ? '本机处理器正在运行，任务将依次执行。' : jobs.some(job => job.state === 'queued') ? '任务已排队，处理器尚未启动。请检查服务状态。' : '本机任务状态 · 每 3 秒更新';
    if (key === jobsRenderKey) return;
    jobsRenderKey = key;
    const focusedJob = document.activeElement?.closest('.job')?.dataset.id;
    const focusedAction = focusedJob ? document.activeElement.textContent : null;
    $('jobs').replaceChildren();
    if (!jobs.length) { const empty = document.createElement('div'); empty.className = 'queue-empty'; empty.append(icon('queue'), text('h2','当前没有转写任务'), text('p','打开一场面试，点击“语音转文字”选择模型和处理范围。'), actionButton('返回资料库', () => showView('record'))); $('jobs').append(empty); return; }
    jobs.forEach(job => {
      const card = document.createElement('article'); card.className = 'job'; card.dataset.state = job.state; card.dataset.id = job.id;
      const header = document.createElement('div'); header.className = 'job-heading'; header.append(text('h2', job.title || '转写任务'), text('span', stateLabels[job.state] || job.state, 'job-state'));
      const options = typeof job.options === 'object' && job.options ? job.options : {};
      card.append(header, text('p', [options.model, options.language, options.start ? '从 ' + time(options.start) + ' 开始' : '', options.limit ? '时长 ' + time(options.limit) : ''].filter(Boolean).join(' · '), 'job-meta'));
      if (job.state === 'running' && job.progress) {
        const progress = job.progress, stageNames = {queued:'等待处理',extract:'提取音频',extracting:'提取音频',transcribe:'转写中',transcribing:'转写中',load:'加载模型',loading:'加载模型',save:'保存结果',saving:'保存结果'};
        const hasTotal = Number(progress.total) > 0 && Number.isFinite(Number(progress.done));
        card.append(text('div', (stageNames[progress.stage] || progress.stage || '正在处理') + (hasTotal ? ` · ${progress.done} / ${progress.total}` : ''), 'job-progress-label'));
        const bar = document.createElement('progress'); bar.setAttribute('aria-label','任务进度'); if (hasTotal) { bar.max = Number(progress.total); bar.value = Math.min(bar.max, Math.max(0, Number(progress.done))); } card.append(bar);
      }
      if (job.error) card.append(text('p', job.error, 'job-error'));
      const actions = document.createElement('div'); actions.className = 'job-actions';
      if (job.interview_id) actions.append(actionButton(completed(job.state) ? '查看结果' : '打开记录', () => selectRecord(job.interview_id, completed(job.state) ? job.run_id : undefined)));
      if (job.state === 'queued') actions.append(actionButton('取消排队', async event => {
        const button = event.currentTarget; button.disabled = true;
        try { await api(`/api/queue/${encodeURIComponent(job.id)}/cancel`, {method:'POST', body:{}}); await refreshQueue(); }
        catch (error) { reportError(error); button.disabled = false; }
      }));
      if (job.state === 'failed') actions.append(actionButton('重试', async event => {
        const button = event.currentTarget; button.disabled = true;
        try { await api('/api/queue', {method:'POST', body:{interview_id:job.interview_id, options}}); await refreshQueue(); toast('已重新加入队列'); }
        catch (error) { reportError(error); button.disabled = false; }
      }));
      if (job.state === 'running') actions.append(text('span','处理中，请等待结果', 'helper'));
      card.append(actions); $('jobs').append(card);
    });
    if (focusedJob) {
      const card = [...$('jobs').children].find(element => element.dataset.id === focusedJob);
      const buttons = [...(card?.querySelectorAll('button') || [])];
      (buttons.find(button => button.textContent === focusedAction) || buttons[0])?.focus({preventScroll:true});
    }
  }
  async function refreshQueue() {
    if (!boot) return;
    if (queueRequest) return queueRequest;
    clearTimeout(queueTimer); const version = ++queueVersion;
    queueRequest = (async () => {
      try {
        const result = await api('/api/queue'); if (version !== queueVersion) return;
        queueFailures = 0;
        jobs = result.jobs || []; renderJobs(result.worker_active);
        const finished = jobs.filter(job => previousJobStates.has(job.id) && previousJobStates.get(job.id) !== job.state && completed(job.state));
        previousJobStates = new Map(jobs.map(job => [job.id, job.state]));
        $('connection-state').textContent = '本机工作空间';
        if (finished.length) {
          await refreshLibrary();
          const latest = finished.find(job => job.interview_id === currentId());
          // Refresh available versions without replacing in-flight note edits or moving playback.
          if (latest && current && !navigating) {
            const result = await api(`/api/interviews/${encodeURIComponent(currentId())}${runId() ? `?run=${encodeURIComponent(runId())}` : ''}`);
            if (result.item.id === currentId()) {
              current.runs = result.runs; current.item = result.item;
              renderRuns();
            }
          }
          toast('转写已完成，可从处理队列打开结果');
        }
      } catch (error) {
        queueFailures = Math.min(queueFailures + 1, 5);
        $('connection-state').textContent = '连接中断';
        $('worker-state').textContent = error.message + ' 队列状态已保留，将自动重新连接。';
      }
      finally { queueRequest = null; queueTimer = setTimeout(refreshQueue, Math.min(30000, 3000 * 2 ** Math.max(0, queueFailures - 1))); }
    })();
    return queueRequest;
  }
  $('queue-refresh').addEventListener('click', () => { queueFailures = 0; refreshQueue(); });

  function notesMarkdown() {
    const state = draft(); if (!current || !state) return '';
    const refs = state.citations.map(citation => `- [${time(citation.start)}] ${citation.text}`).join('\n');
    const history = (current.notes || []).map(note => `### ${note.at || ''}\n\n${note.body || ''}`).join('\n\n');
    return `# ${current.item.title}\n\n${state.body}\n\n## 原文引用\n\n${refs || '暂无引用'}\n${history ? '\n## 历史笔记\n\n' + history + '\n' : ''}`;
  }
  async function prepareExport() {
    const generation = ++exportGeneration, format = $('export-format').value;
    $('download-export').disabled = true; formError('export-error', null); $('export-preview').value = '正在准备…';
    try {
      let content;
      if (format === 'notes') content = notesMarkdown();
      else content = await api(`/api/interviews/${encodeURIComponent(currentId())}/export?format=${encodeURIComponent(format)}${runId() ? `&run=${encodeURIComponent(runId())}` : ''}`, {responseText:true});
      if (generation !== exportGeneration) return;
      exportContent = content; exportExtension = ['notes','review'].includes(format) ? 'md' : format; $('export-preview').value = content; $('download-export').disabled = false;
    } catch (error) { if (generation === exportGeneration) { $('export-preview').value = ''; formError('export-error', error); } }
  }
  function openExport(format = 'notes') { if (!current) return; $('export-format').value = format; [...$('export-format').options].forEach(option => option.disabled = option.value !== 'notes' && !runId()); openDialog('export-dialog'); prepareExport(); }
  $('export-open').addEventListener('click', () => openExport()); $('export-format').addEventListener('change', prepareExport);
  $('download-export').addEventListener('click', async () => {
    const suggestedName = `echo-${$('export-format').value === 'notes' ? 'notes' : 'transcript'}.${exportExtension}`;
    $('download-export').disabled = true; formError('export-error', null);
    try {
        const url = URL.createObjectURL(new Blob([exportContent], {type:'text/plain;charset=utf-8'})); const link = document.createElement('a'); link.href = url; link.download = suggestedName; document.body.append(link); link.click(); link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000); toast('已请求浏览器下载；也可复制上方内容。');
    } catch (error) { formError('export-error', error); }
    finally { $('download-export').disabled = false; }
  });
  $('record-info').addEventListener('click', () => {
    if (!current) return; const metadata = current.item.metadata || {};
    projectOptions($('meta-project_id'), '未分组');
    ['company','round','date','tags','project_id'].forEach(key => $('meta-' + key).value = Array.isArray(metadata[key]) ? metadata[key].join(', ') : metadata[key] || '');
    $('record-path').textContent = current.item.path || ''; formError('metadata-error', null); openDialog('metadata-dialog');
  });
  $('metadata-form').addEventListener('submit', async event => {
    event.preventDefault(); $('metadata-submit').disabled = true; formError('metadata-error', null);
    try { const metadata = Object.fromEntries(['company','round','date','tags','project_id'].map(key => [key, $('meta-' + key).value.trim()])); await api(`/api/interviews/${encodeURIComponent(currentId())}/metadata`, {method:'POST', body:metadata}); current.item.metadata = {...current.item.metadata, ...metadata}; $('record-meta').textContent = recordMeta({...current.item, duration:current.duration}); await refreshLibrary(); $('metadata-dialog').close(); toast('记录信息已保存'); }
    catch (error) { formError('metadata-error', error); }
    finally { $('metadata-submit').disabled = false; }
  });
  async function initialize() {
    if (initializing) return;
    initializing = true;
    $('global-retry').disabled = true;
    try {
      await renewSession(); $('app-version').textContent = 'ECHO / ' + boot.version; $('connection-state').textContent = '本机工作空间';
      await refreshLibrary(); $('global-error').hidden = true; queueFailures = 0;
      // Reconnecting updates the session and navigation only. The open record,
      // editor, unsaved revision, selected run, and media element stay intact.
      if (!current) showWelcome();
      await refreshQueue();
      if (!current && records.length) await selectRecord(records[0].id);
    } catch (error) {
      $('connection-state').textContent = '尚未连接'; $('welcome-title').textContent = '工作空间暂时无法打开'; $('welcome-copy').textContent = error.message; $('welcome-actions').hidden = true; reportError(error);
    } finally { initializing = false; $('global-retry').disabled = false; }
  }
  applyTheme(); updateWidth(notesWidth); $('notes-toggle').setAttribute('aria-expanded', String(notesVisible())); initialize();

})();
