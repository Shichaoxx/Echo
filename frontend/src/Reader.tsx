/// <reference types="vite/client" />
import { Fragment, useEffect, useRef, useState } from 'react';
import type { Block, Edit, Minutes, ReaderProps, Ref } from './types';
import './reader.css';

const labels = {
  summary: '总结',
  chapter: '智能章节',
  decision: '关键决策',
  quote: '金句时刻',
  related: '相关资料',
};
type Section = keyof typeof labels;
const formatTime = (seconds: number) => {
  const n = Math.floor(Math.max(0, seconds));
  const parts = [Math.floor(n / 60) % 60, n % 60].map((v) => String(v).padStart(2, '0'));
  if (n >= 3600) parts.unshift(String(Math.floor(n / 3600)));
  return parts.join(':');
};
const byTime = (a: Block, b: Block) => firstRef(a).start - firstRef(b).start;
const firstRef = (block: Block) =>
  block.evidence.reduce((first, ref) => (ref.start < first.start ? ref : first));
type Cue = Pick<Ref, 'start' | 'end' | 'playable'> & { label?: '章节回听' | '追问回听' };

function discussionCue(block: Block, doc: Minutes, duration: number | null): Cue {
  const first = firstRef(block);
  const chapter = block.type === 'chapter';
  const boundaries = doc.blocks.filter((b) => b.type === 'chapter' || (!chapter && b.type === 'followup'));
  const following = boundaries.map((b) => firstRef(b).start).filter((t) => t > first.start);
  const last = chapter ? doc.scope.end : Math.max(...block.evidence.map((ref) => ref.end));
  const end = Math.min(following.length ? Math.min(...following) : last, doc.scope.end, duration ?? Infinity);
  return {
    start: first.start,
    end,
    playable: first.playable && first.start >= doc.scope.start,
    label: chapter ? '章节回听' : '追问回听',
  };
}

function playbackIssue(media: MediaError | null, reason?: Error) {
  if (reason?.name === 'NotAllowedError') return '浏览器已暂停音频。请点击播放继续回听。';
  if (media?.code === 3) return '浏览器无法解码这份音频，请检查媒体格式。';
  if (media?.code === 2) return '读取原声时连接中断。请重试，或在“转写与录音”中核对源文件。';
  return '原声无法加载：媒体格式可能不受支持，或源文件已经改变。仍可展开原文核对。';
}

const scopeText = (doc: Minutes) => {
  if (doc.scope.kind === 'full') return '完整转写';
  const span = formatTime(doc.scope.start) + '–' + formatTime(doc.scope.end);
  return (doc.scope.kind === 'partial' ? '片段转写 ' : '已覆盖 ') + span;
};

function saveFile(content: string, filename: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const link = window.document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportMinutes(title: string, doc: Minutes, drafts?: Edit[]) {
  const literal = (s: string) =>
    s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/([\\`*_\[\]#!|~])/g, '\\$1');
  const blocks = doc.blocks.map((b) => ({ ...b, ...drafts?.find((edit) => edit.id === b.id) }));
  const find = (id: string) => blocks.find((b) => b.id === id)!;
  const lines = [
    '# ' + literal(title),
    '',
    (drafts ? '本地修订草稿' : '智能纪要') + ' · 第 ' + doc.revision + ' 版 · ' + scopeText(doc),
  ];
  const writeBlock = (b: Block, depth = '###') => {
    lines.push('', depth + ' ' + literal(b.title), '');
    if (b.type === 'quote') {
      lines.push('> ' + literal(b.text).replaceAll('\n', '\n> '));
      if (b.interpretation) lines.push('', '解读 · ' + literal(b.interpretation));
    } else lines.push(literal(b.text));
    lines.push('', doc.verified ? '原文证据' : '历史原文快照');
    b.evidence.forEach((r) =>
      lines.push(
        '',
        '> ' +
          formatTime(r.start) +
          '–' +
          formatTime(r.end) +
          '　' +
          literal(r.text).replaceAll('\n', '\n> '),
      ),
    );
  };
  for (const kind of ['summary', 'chapter', 'decision', 'quote'] as const) {
    const items = blocks.filter((b) => b.type === kind);
    if (!items.length) continue;
    lines.push('', '## ' + labels[kind]);
    if (kind === 'chapter') items.sort(byTime);
    items.forEach((b, i) => {
      writeBlock(b);
      if (kind === 'summary' && !i && doc.overview) {
        lines.push('', '### ' + literal(doc.overview.title));
        for (const group of doc.overview.groups) {
          lines.push(
            '',
            '#### ' + literal(group.title),
            '',
            ...group.block_ids.map((id) => '- ' + literal(find(id).title)),
          );
        }
      }
      if (kind === 'chapter')
        blocks
          .filter((q) => q.type === 'followup' && q.parent_id === b.id)
          .sort(byTime)
          .forEach((q) => writeBlock(q, '####'));
    });
    if (kind === 'summary' && doc.comparison) {
      const cell = (s: string) => literal(s).replace(/[\r\n]+/g, ' ');
      lines.push(
        '',
        '### ' + literal(doc.comparison.title),
        '',
        '| 主题 | 要点 | 引文时间 |',
        '| --- | --- | --- |',
      );
      for (const id of doc.comparison.block_ids) {
        const b = find(id);
        lines.push(
          '| ' +
            [cell(b.title), cell(b.text), b.evidence.map((r) => formatTime(r.start)).join('、')].join(' | ') +
            ' |',
        );
      }
    }
  }
  lines.push('', '## 相关资料', '', '来源转写 · ' + literal(doc.source_run_id));
  saveFile(
    lines.join('\n'),
    title.replace(/[/\\:\x00-\x1f]/g, '-') + (drafts ? '-草稿' : '-纪要') + '.md',
    'text/markdown;charset=utf-8',
  );
}

const errorText = (reason: unknown) => (reason instanceof Error ? reason.message : '操作失败，请稍后重试。');
type Draft = { base: number; edits: Edit[] };

export default function Reader({
  recording,
  document: doc,
  history,
  onLibrary,
  onGenerate,
  onImport,
  onHistory,
  onSave,
  onSources,
}: ReaderProps) {
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [active, setActive] = useState<Section>('summary');
  const scroll = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDetailsElement>(null);
  const mobile = useRef<HTMLDetailsElement>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const sequence = useRef(0);
  const pendingSeek = useRef<number | null>(null);
  const [clip, setClip] = useState<Cue | null>(null);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [playError, setPlayError] = useState('');
  const [mediaFailed, setMediaFailed] = useState(false);
  const regions = (Object.keys(labels) as Section[]).filter(
    (key) => key === 'related' || doc.blocks.some((b) => b.type === key),
  );
  const table = new Map(doc.blocks.map((b) => [b.id, b]));
  const snapshots = [
    ...new Map(doc.blocks.flatMap((b) => b.evidence.map((r) => [r.index, r] as const))).values(),
  ].sort((a, b) => a.start - b.start);
  const latest = Math.max(doc.revision, recording.latest_revision, ...history.map((r) => r.revision));
  const dirty = !!draft?.edits.some((edit) => {
    const block = table.get(edit.id);
    return (
      block?.title !== edit.title ||
      block.text !== edit.text ||
      (block.interpretation || '') !== (edit.interpretation || '')
    );
  });

  function closePlayback() {
    sequence.current++;
    pendingSeek.current = null;
    audio.current?.pause();
    setClip(null);
    setPlaying(false);
    setPlayError('');
  }
  useEffect(() => {
    // A guarded stream belongs to exactly one saved revision.
    closePlayback();
    setMediaFailed(false);
    return () => {
      sequence.current++;
    };
  }, [doc.recording_id, doc.source_run_id, doc.revision, doc.media_url]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    const route = (event: HashChangeEvent) => {
      if (!busy && window.confirm('离开并放弃尚未保存的纪要修订？')) {
        setDraft(null);
        return;
      }
      window.history.replaceState(window.history.state, '', event.oldURL);
      event.stopImmediatePropagation();
    };
    window.addEventListener('beforeunload', warn);
    window.addEventListener('hashchange', route, true);
    return () => {
      window.removeEventListener('beforeunload', warn);
      window.removeEventListener('hashchange', route, true);
    };
  }, [dirty, busy]);
  useEffect(() => {
    function dismiss(e: PointerEvent | KeyboardEvent) {
      const esc = e instanceof KeyboardEvent && e.key === 'Escape';
      if (e instanceof KeyboardEvent && !esc) return;
      for (const node of [menu.current, mobile.current]) {
        if (!node?.open || (!esc && e.target instanceof Node && node.contains(e.target))) continue;
        node.open = false;
        if (esc) node.querySelector('summary')?.focus();
      }
    }
    window.addEventListener('pointerdown', dismiss);
    window.addEventListener('keydown', dismiss);
    return () => {
      window.removeEventListener('pointerdown', dismiss);
      window.removeEventListener('keydown', dismiss);
    };
  }, []);

  function allowLeave() {
    return !busy && (!dirty || window.confirm('放弃尚未保存的纪要修订？'));
  }
  function navigate(fn: () => void) {
    if (!allowLeave()) return;
    setDraft(null);
    closePlayback();
    if (menu.current) menu.current.open = false;
    fn();
  }
  function edit() {
    setNotice('');
    setDraft({
      base: doc.revision,
      edits: doc.blocks.map(({ id, type, title, text, interpretation }) =>
        type === 'quote' ? { id, title, text, interpretation: interpretation || '' } : { id, title, text },
      ),
    });
  }
  async function save() {
    if (!draft || busy) return;
    setBusy(true);
    setNotice('');
    try {
      await onSave(draft.base, draft.edits);
      setDraft(null);
      setNotice('修订已保存');
    } catch (error) {
      setNotice(errorText(error) + '　你的修订草稿仍保留在此页。');
    } finally {
      setBusy(false);
    }
  }
  async function selectRevision(value: number) {
    if (busy || draft || value === doc.revision) return;
    setBusy(true);
    setNotice('');
    closePlayback();
    try {
      await onHistory(value);
      if (menu.current) {
        menu.current.open = false;
        menu.current.querySelector('summary')?.focus();
      }
    } catch (error) {
      setNotice(errorText(error));
    } finally {
      setBusy(false);
    }
  }
  function update(id: string, key: 'title' | 'text' | 'interpretation', value: string) {
    setDraft(
      (current) =>
        current && {
          ...current,
          edits: current.edits.map((e) => (e.id === id ? { ...e, [key]: value } : e)),
        },
    );
  }
  function jump(section: Section) {
    if (mobile.current) mobile.current.open = false;
    const node = window.document.getElementById('ed-' + section);
    node?.scrollIntoView({ block: 'start' });
    node?.focus({ preventScroll: true });
    setActive(section);
  }
  function updateActive() {
    const container = scroll.current;
    if (!container) return;
    const top = container.getBoundingClientRect().top + 72;
    let next = regions[0];
    for (const section of regions) {
      const el = window.document.getElementById('ed-' + section);
      if (el && el.getBoundingClientRect().top <= top) next = section;
    }
    if (container.scrollHeight - container.clientHeight - container.scrollTop < 2)
      next = regions[regions.length - 1];
    setActive(next);
  }
  function reveal(id: string) {
    const section = window.document.getElementById('ed-block-' + id);
    const proof = section?.querySelector('details');
    if (proof) proof.open = true;
    section?.scrollIntoView({ block: 'start' });
    section?.focus({ preventScroll: true });
  }
  function canPlay(r: Cue) {
    return !!doc.media_url && doc.verified && r.playable && r.start >= doc.media_offset && r.end > r.start;
  }
  function play(r: Cue, start = r.start) {
    const player = audio.current;
    if (!canPlay(r) || !player || !doc.media_url) return;
    const request = ++sequence.current;
    setPlayError('');
    setClip(r);
    setPosition(start);
    if (player.getAttribute('src') !== doc.media_url) player.src = doc.media_url;
    else if (mediaFailed) {
      setMediaFailed(false);
      player.load();
    }
    const target = Math.max(0, start - doc.media_offset);
    if (player.readyState < 1) pendingSeek.current = target;
    else {
      pendingSeek.current = null;
      player.currentTime = target;
    }
    void player.play().catch((error: Error) => {
      if (request === sequence.current && error.name !== 'AbortError') {
        setPlaying(false);
        setPlayError(playbackIssue(player.error, error));
      }
    });
  }
  function tick() {
    const node = audio.current;
    if (!node || !clip) return;
    const here = doc.media_offset + node.currentTime;
    if (here >= clip.end && !node.paused) node.pause();
    setPosition(Math.min(clip.end, Math.max(clip.start, here)));
  }
  function stamp(r: Cue, question?: string) {
    const time = formatTime(r.start);
    const label = r.label || '回听原声';
    return (
      <button
        type="button"
        className={question ? 'ed-jump ed-heading-jump' : 'ed-jump'}
        disabled={!canPlay(r)}
        aria-label={[canPlay(r) ? label : '无法回听', time, question].filter(Boolean).join(' ')}
        title={canPlay(r) ? label + ' ' + time + '–' + formatTime(r.end) : '原文快照可读，当前无法回听'}
        onClick={() => play(r)}
      >
        {question ? (
          <>
            <span className="ed-heading-time">{time}</span>
            {r.label === '追问回听' && <span className="ed-followup-label">追问</span>}
            <span className="ed-question-text">{question}</span>
          </>
        ) : (
          time
        )}
      </button>
    );
  }
  function toc() {
    return (
      <nav className="ed-toc" aria-label="文档目录">
        {regions.map((id) => (
          <button key={id} aria-current={active === id ? 'location' : undefined} onClick={() => jump(id)}>
            {labels[id]}
          </button>
        ))}
      </nav>
    );
  }
  function proof(block: Block) {
    return (
      <details className="ed-proof">
        <summary>
          {doc.verified ? '查看原文' : '历史原文快照'} · {block.evidence.length} 段
        </summary>
        {block.evidence.map((r) => (
          <div className="ed-source" key={r.index}>
            <blockquote>{r.text}</blockquote>
            {stamp(r)}
            <span className="ed-subtle">
              {' '}
              – {formatTime(r.end)}
              {!canPlay(r) ? ' · 无法回听' : ''}
            </span>
          </div>
        ))}
      </details>
    );
  }
  function renderBlock(block: Block, nested = false) {
    const local = draft?.edits.find((e) => e.id === block.id);
    const Heading = nested ? 'h4' : 'h3';
    const timed = block.type === 'chapter' || block.type === 'followup';
    return (
      <section
        className={'ed-block' + (nested ? ' ed-followup' : '')}
        tabIndex={-1}
        id={'ed-block-' + block.id}
        key={block.id}
      >
        {local ? (
          <div className="ed-editor">
            <label>
              标题
              <input
                value={local.title}
                disabled={busy}
                onChange={(e) => update(block.id, 'title', e.target.value)}
              />
            </label>
            <label>
              {block.type === 'quote' ? '逐字引文' : '正文'}
              <textarea
                rows={3}
                value={local.text}
                disabled={busy}
                onChange={(e) => update(block.id, 'text', e.target.value)}
              />
            </label>
            {block.type === 'quote' && (
              <label>
                解读（可选）
                <textarea
                  rows={2}
                  value={local.interpretation || ''}
                  disabled={busy}
                  onChange={(e) => update(block.id, 'interpretation', e.target.value)}
                />
              </label>
            )}
          </div>
        ) : (
          <>
            {(timed || block.title !== labels[block.type as Section]) && (
              <Heading>
                {timed ? stamp(discussionCue(block, doc, recording.duration), block.title) : block.title}
              </Heading>
            )}
            {block.type === 'quote' ? (
              <>
                <blockquote className="ed-quote">{block.text}</blockquote>
                {block.interpretation && (
                  <p className="ed-interpretation">
                    <strong>解读</strong>
                    {block.interpretation}
                  </p>
                )}
              </>
            ) : (
              <p className="ed-prose">{block.text}</p>
            )}
          </>
        )}
        {proof(block)}
        {block.type === 'chapter' &&
          doc.blocks
            .filter((b) => b.type === 'followup' && b.parent_id === block.id)
            .sort(byTime)
            .map((b) => renderBlock(b, true))}
      </section>
    );
  }
  function board() {
    const overview = doc.overview;
    if (!overview || draft) return null;
    return (
      <section className="ed-board">
        <h3>{overview.title}</h3>
        <div className="ed-board-groups">
          {overview.groups.map((group, i) => (
            <section key={i} className="ed-board-group">
              <h4>{group.title}</h4>
              <ul>
                {group.block_ids.map((id) => {
                  const block = table.get(id);
                  if (!block) return null;
                  const lead = block.text.match(/^[\s\S]*?(?:[。！？]|[.!?](?=\s|$))/)?.[0] || block.text;
                  return (
                    <li key={id}>
                      <button onClick={() => reveal(id)}>{block.title}</button>
                      {block.type !== 'summary' && lead !== block.title && (
                        <p className="ed-board-detail">{lead}</p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      </section>
    );
  }
  function comparison() {
    if (!doc.comparison || draft) return null;
    return (
      <div className="ed-table">
        <table>
          <caption>{doc.comparison.title}</caption>
          <thead>
            <tr>
              <th>主题</th>
              <th>要点</th>
              <th>原文</th>
            </tr>
          </thead>
          <tbody>
            {doc.comparison.block_ids.map((id) => {
              const b = table.get(id);
              if (!b) return null;
              return (
                <tr key={id}>
                  <th scope="row">{b.title}</th>
                  <td>{b.text}</td>
                  <td>
                    <button className="ed-jump" onClick={() => reveal(id)}>
                      查看引文
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  }
  function related() {
    return (
      <>
        <div className="ed-related">
          <button onClick={() => navigate(onSources)}>转写与录音 ↗</button>
        </div>
        <details className="ed-proof">
          <summary>引用的逐字稿片段 · {snapshots.length} 段</summary>
          {snapshots.map((r) => (
            <div key={r.index} className="ed-source">
              {stamp(r)}
              <blockquote>{r.text}</blockquote>
            </div>
          ))}
        </details>
        <details className="ed-proof">
          <summary>来源版本</summary>
          <p>转写版本 · {doc.source_run_id}</p>
          <p>纪要保存时间 · {doc.created_at}</p>
        </details>
      </>
    );
  }
  return (
    <div
      className="echo-doc"
      onKeyDown={(e) => {
        if (draft && (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
          e.preventDefault();
          void save();
        }
      }}
    >
      <header className="ed-bar">
        <div className="ed-breadcrumb">
          <button
            className="ed-wordmark"
            title="返回最近纪要"
            aria-label="返回最近纪要"
            onClick={() => navigate(onLibrary)}
          >
            Echo
          </button>
          <button className="ed-bar-name" onClick={() => navigate(onLibrary)}>
            最近纪要
          </button>
          <details ref={mobile} className="ed-mobile">
            <summary>目录</summary>
            {toc()}
          </details>
        </div>
        <div className="ed-bar-actions">
          {draft ? (
            <>
              <button
                className="ed-quiet"
                disabled={busy}
                onClick={() => {
                  if (allowLeave()) {
                    setDraft(null);
                    setNotice('');
                  }
                }}
              >
                取消
              </button>
              <button className="ed-button ed-primary" disabled={busy || !dirty} onClick={save}>
                {busy ? '保存中…' : '保存修订'}
              </button>
            </>
          ) : (
            doc.revision === latest && (
              <button className="ed-button" disabled={busy} onClick={edit}>
                修订正文
              </button>
            )
          )}
          <details ref={menu} className="ed-menu">
            <summary aria-label="文档操作">
              操作 <span aria-hidden="true">⌄</span>
            </summary>
            <div className="ed-menu-panel">
              <label>
                历史版本
                <select
                  aria-label="历史版本"
                  value={doc.revision}
                  disabled={busy || !!draft}
                  onChange={(e) => void selectRevision(Number(e.target.value))}
                >
                  {history.map((r) => (
                    <option key={r.revision} value={r.revision}>
                      第 {r.revision} 版 · {r.author === 'user' ? '人工修订' : '生成稿'}
                    </option>
                  ))}
                </select>
              </label>
              <button
                onClick={() => {
                  exportMinutes(recording.title, doc, draft?.edits);
                  if (menu.current) menu.current.open = false;
                }}
              >
                {draft ? '导出修订草稿' : '导出 Markdown'}
              </button>
              <button onClick={() => navigate(onSources)}>转写与录音</button>
              <button onClick={() => navigate(onGenerate)}>生成纪要交接</button>
              <button onClick={() => navigate(onImport)}>导入纪要</button>
            </div>
          </details>
        </div>
      </header>
      <div className="ed-shell">
        <aside className="ed-aside">
          <p className="ed-aside-title">目录</p>
          {toc()}
        </aside>
        <div className="ed-scroll" ref={scroll} onScroll={updateActive}>
          <main className="ed-article">
            <header className="ed-heading">
              <p className="ed-doctype">智能纪要</p>
              <h1>{recording.title}</h1>
              <p className="ed-meta">
                {recording.date && <span>{recording.date}</span>}
                {recording.duration != null && <span>{formatTime(recording.duration)}</span>}
                <span>{scopeText(doc)}</span>
                <span>
                  第 {doc.revision} 版 · {doc.author === 'user' ? '人工修订' : '生成稿'}
                </span>
              </p>
            </header>
            {doc.warning && (
              <p className="ed-alert" role="status">
                {doc.warning}
              </p>
            )}
            {!doc.verified && !doc.warning && (
              <p className="ed-alert" role="status">
                来源转写已无法核对。以下保留保存时的原文快照。
              </p>
            )}
            {notice && (
              <p className="ed-alert" role="status">
                {notice}
              </p>
            )}
            {regions.map((kind) => {
              const items = doc.blocks.filter((b) => b.type === kind);
              if (kind === 'chapter') items.sort(byTime);
              return (
                <section className="ed-section" id={'ed-' + kind} key={kind} tabIndex={-1}>
                  <h2>{labels[kind]}</h2>
                  {kind === 'related' ? (
                    related()
                  ) : (
                    <>
                      {items.map((block, i) => (
                        <Fragment key={block.id}>
                          {renderBlock(block)}
                          {kind === 'summary' && i === 0 && board()}
                        </Fragment>
                      ))}
                      {kind === 'summary' && comparison()}
                    </>
                  )}
                </section>
              );
            })}
          </main>
        </div>
      </div>
      <audio
        ref={audio}
        preload="none"
        onLoadedMetadata={(event) => {
          if (pendingSeek.current !== null) {
            event.currentTarget.currentTime = pendingSeek.current;
            pendingSeek.current = null;
          }
        }}
        onTimeUpdate={tick}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onError={(event) => {
          setPlaying(false);
          setMediaFailed(true);
          setPlayError(playbackIssue(event.currentTarget.error));
        }}
      />
      {clip && (
        <footer className="ed-player" aria-label={clip.label || '引文回听'}>
          <button
            className="ed-play"
            aria-label={playing ? '暂停回听' : mediaFailed ? '重试回听' : '继续回听'}
            disabled={!canPlay(clip)}
            onClick={() => {
              if (playing) audio.current?.pause();
              else play(clip, position >= clip.end ? clip.start : position);
            }}
          >
            {playing ? 'Ⅱ' : '▶'}
          </button>
          <span className="ed-player-label">{clip.label || '原声回听'}</span>
          <span className="ed-player-time">
            {formatTime(position)} / {formatTime(clip.end)}
          </span>
          <input
            type="range"
            aria-label={(clip.label || '引文') + '播放位置'}
            disabled={!canPlay(clip)}
            min={clip.start}
            max={clip.end}
            step={0.01}
            value={position}
            onChange={(e) => {
              const value = Number(e.target.value);
              setPosition(value);
              if (audio.current) audio.current.currentTime = value - doc.media_offset;
            }}
          />
          <button className="ed-quiet" onClick={closePlayback}>
            收起
          </button>
          {playError && (
            <span role="status" className="ed-subtle">
              {playError}
            </span>
          )}
        </footer>
      )}
    </div>
  );
}
