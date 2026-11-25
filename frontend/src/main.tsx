import { useCallback, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Reader from './Reader';
import { Dialog, HandoffDialog, ImportDialog, UploadDialog, runLabel } from './Dialogs';
import { explain, get, recordingPath, recordingUrl, send, timecode } from './api';
import type { Detail, Edit, ImportResult, Minutes, Recording, Revision } from './types';
import './library.css';

type Modal = 'handoff' | 'import' | 'legacy' | 'sources' | null;
const toLibrary = () => {
  window.location.hash = '/library';
};
const openRecord = (id: string) => {
  window.location.hash = recordingUrl(id);
};

function BrandHeader({ onUpload }: { onUpload?: () => void }) {
  return (
    <header className="library-bar">
      <a href="#/library" aria-label="Echo 资料库">
        Echo
      </a>
      {onUpload && (
        <button className="lib-primary" onClick={onUpload}>
          导入录音
        </button>
      )}
    </header>
  );
}

function Library({ start }: { start: boolean }) {
  const [items, setItems] = useState<Recording[] | null>(null);
  const [error, setError] = useState(''),
    [attempt, setAttempt] = useState(0),
    [upload, setUpload] = useState(false);
  useEffect(() => {
    const ctrl = new AbortController();
    setError('');
    get<{ items: Recording[] }>('/api/recordings', ctrl.signal)
      .then(({ items }) => {
        if (ctrl.signal.aborted) return;
        const doc = items.find((r) => r.latest_revision > 0);
        if (start && doc) {
          window.location.replace(recordingUrl(doc.id));
          return;
        }
        setItems(items);
      })
      .catch((err) => {
        if (!ctrl.signal.aborted) setError(explain(err));
      });
    return () => ctrl.abort();
  }, [start, attempt]);
  return (
    <div className="library">
      <BrandHeader onUpload={() => setUpload(true)} />
      <main className="library-main">
        <h1>最近纪要</h1>
        {error ? (
          <div role="alert">
            <p>{error}</p>
            <button onClick={() => setAttempt((n) => n + 1)}>重新连接</button>
          </div>
        ) : !items ? (
          <p role="status">正在打开资料库…</p>
        ) : !items.length ? (
          <div className="library-empty">
            <h2>导入第一场面试录音</h2>
            <p>已有 Echo 转写的录音，可以在导入后直接选择旧转写。</p>
            <button className="lib-primary" onClick={() => setUpload(true)}>
              选择录音
            </button>
          </div>
        ) : (
          <ul className="library-list">
            {items.map((r) => (
              <li key={r.id}>
                <a href={recordingUrl(r.id)}>
                  <span className="doc-icon" aria-hidden="true">
                    ≡
                  </span>
                  <span>
                    <strong>{r.title}</strong>
                    <small>
                      {[
                        r.date,
                        r.duration != null ? timecode(r.duration) : '',
                        r.latest_revision ? '第 ' + r.latest_revision + ' 版' : '待生成纪要',
                      ]
                        .filter(Boolean)
                        .join('　·　')}
                    </small>
                  </span>
                  <span className="row-arrow" aria-hidden="true">
                    ↗
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </main>
      {upload && (
        <UploadDialog
          onClose={() => setUpload(false)}
          onCreated={(id) => {
            setUpload(false);
            openRecord(id);
          }}
        />
      )}
    </div>
  );
}

function Sources({
  detail,
  onClose,
  onLegacy,
  onStart,
  working,
}: {
  detail: Detail;
  onClose: () => void;
  onLegacy: () => void;
  onStart: () => Promise<void>;
  working: boolean;
}) {
  const [run, setRun] = useState(detail.document?.source_run_id || detail.runs[0]?.id || '');
  const [data, setData] = useState<{
    segments: { index: number; text: string; start: number }[];
    warning?: string;
  } | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const ctrl = new AbortController();
    setData(null);
    setError('');
    if (run)
      get<NonNullable<typeof data>>(
        recordingPath(detail.recording.id) + '/runs/' + encodeURIComponent(run),
        ctrl.signal,
      )
        .then((value) => {
          if (!ctrl.signal.aborted) setData(value);
        })
        .catch((err) => {
          if (!ctrl.signal.aborted) setError(explain(err));
        });
    return () => ctrl.abort();
  }, [run, detail.recording.id]);
  return (
    <Dialog title="转写与录音" onClose={onClose}>
      <p>
        {detail.recording.title}
        {detail.recording.duration != null ? '　·　' + timecode(detail.recording.duration) : ''}
      </p>
      {detail.runs.length > 0 && (
        <label>
          查看逐字稿
          <select value={run} onChange={(e) => setRun(e.target.value)}>
            <option value="">选择转写版本</option>
            {detail.runs.map((r) => (
              <option key={r.id} value={r.id}>
                {runLabel(r)}
              </option>
            ))}
          </select>
        </label>
      )}
      {data?.warning && <p role="status">{data.warning}</p>}
      {data && (
        <ol className="source-preview">
          {data.segments.map((s, i) => (
            <li key={s.index ?? i}>
              <time>{timecode(s.start)}</time>
              {s.text}
            </li>
          ))}
        </ol>
      )}
      {error && (
        <p className="library-error" role="alert">
          {error}
        </p>
      )}
      {detail.job?.state === 'failed' && <p role="alert">{detail.job.error}</p>}
      <footer>
        <button onClick={onLegacy}>导入旧转写</button>
        <button
          className="lib-primary"
          disabled={working}
          onClick={() => {
            setError('');
            onStart().catch((err) => setError(explain(err)));
          }}
        >
          {working ? '正在转写…' : '开始本地转写'}
        </button>
      </footer>
    </Dialog>
  );
}

function RecordPage({ id }: { id: string }) {
  const [detail, setDetail] = useState<Detail | null>(null),
    [modal, setModal] = useState<Modal>(null);
  const [error, setError] = useState(''),
    [starting, setStarting] = useState(false);
  const alive = useRef(true),
    request = useRef(0),
    selectedRevision = useRef<number | null>(null);
  const base = recordingPath(id);
  const refresh = useCallback(async () => {
    const ticket = ++request.current;
    const next = await get<Detail>(base);
    const wanted = selectedRevision.current;
    if (wanted && next.document?.revision !== wanted) {
      const selected = await get<{ document: Minutes; history: Revision[] }>(
        base + '/minutes?revision=' + wanted,
      );
      next.document = selected.document;
      next.history = selected.history;
    }
    if (alive.current && ticket === request.current) {
      setDetail(next);
      setError('');
    }
  }, [base]);
  useEffect(() => {
    alive.current = true;
    refresh().catch((err) => {
      if (alive.current) setError(explain(err));
    });
    return () => {
      alive.current = false;
      ++request.current;
    };
  }, [refresh]);
  const transcribing = ['queued', 'running'].includes(detail?.job?.state || '');
  useEffect(() => {
    if (!transcribing) return;
    let active = true,
      timer: ReturnType<typeof setTimeout>;
    async function update() {
      try {
        await refresh();
      } catch (err) {
        if (active) setError(explain(err));
      }
      if (active) timer = setTimeout(update, 1500);
    }
    timer = setTimeout(update, 1000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [transcribing, refresh]);
  useEffect(() => {
    if (detail) document.title = detail.recording.title + ' · Echo';
    return () => {
      document.title = 'Echo · 面试纪要';
    };
  }, [detail?.recording.title]);
  async function startASR() {
    if (starting || transcribing) return;
    setStarting(true);
    try {
      await send(base + '/transcribe', {});
      await refresh();
    } finally {
      if (alive.current) setStarting(false);
    }
  }
  async function selectHistory(revision: number) {
    const ticket = ++request.current;
    const response = await get<{ document: Minutes; history: Revision[] }>(
      base + '/minutes?revision=' + revision,
    );
    if (alive.current && ticket === request.current) {
      selectedRevision.current = revision;
      setDetail((prev) => prev && { ...prev, ...response });
    }
  }
  function acceptRevision(result: { document: Minutes; history: Revision[] }) {
    if (!alive.current) return;
    ++request.current;
    selectedRevision.current = null;
    setDetail(
      (prev) =>
        prev && {
          ...prev,
          ...result,
          recording: { ...prev.recording, latest_revision: result.history[0].revision },
        },
    );
    setError('');
  }
  async function save(expected_revision: number, edits: Edit[]) {
    acceptRevision(await send(base + '/minutes/revise', { expected_revision, edits }));
  }
  function imported(result: ImportResult) {
    if (!alive.current) return;
    if ('document' in result) acceptRevision(result);
    else {
      ++request.current;
      setDetail((prev) => prev && { ...prev, runs: [result.run, ...prev.runs] });
    }
    setModal(null);
  }
  if (!detail)
    return (
      <div className="library">
        <BrandHeader />
        <main className="library-main">
          {error ? (
            <>
              <p role="alert">{error}</p>
              <button onClick={() => refresh().catch((err) => setError(explain(err)))}>重试</button>
            </>
          ) : (
            <p role="status">正在读取面试记录…</p>
          )}
        </main>
      </div>
    );
  const doc = detail.document,
    rec = detail.recording;
  return (
    <>
      {doc ? (
        <Reader
          recording={rec}
          document={doc}
          history={detail.history}
          onLibrary={toLibrary}
          onSources={() => setModal('sources')}
          onHistory={selectHistory}
          onSave={save}
          onGenerate={() => setModal('handoff')}
          onImport={() => setModal('import')}
        />
      ) : (
        <div className="library">
          <BrandHeader />
          <main className="library-main recording-start">
            <a className="return-link" href="#/library">
              ← 最近纪要
            </a>
            <h1>{rec.title}</h1>
            <p className="lib-meta">
              {[rec.date, rec.duration == null ? '' : timecode(rec.duration)].filter(Boolean).join('　·　')}
            </p>
            {detail.runs.length ? (
              <>
                <h2>从逐字稿生成纪要</h2>
                <p>将转写交给 Codex，导回生成的文件就能开始阅读。</p>
                <div className="lib-row">
                  <button className="lib-primary" onClick={() => setModal('handoff')}>
                    交给 Codex 生成
                  </button>
                  <button onClick={() => setModal('import')}>导入纪要</button>
                  <button onClick={() => setModal('sources')}>查看转写</button>
                </div>
              </>
            ) : (
              <>
                <h2>准备这场面试的逐字稿</h2>
                <p>可以在 Mac 上转写，也可以使用这段录音已有的 Echo 转写。</p>
                <div className="lib-row">
                  <button
                    className="lib-primary"
                    disabled={transcribing || starting}
                    onClick={() => {
                      setError('');
                      startASR().catch((err) => setError(explain(err)));
                    }}
                  >
                    {transcribing || starting ? '正在本机转写…' : '开始本地转写'}
                  </button>
                  <button onClick={() => setModal('legacy')}>导入旧转写</button>
                </div>
              </>
            )}
            {detail.job?.state === 'failed' && (
              <p role="alert" className="library-error">
                {detail.job.error}
              </p>
            )}
            {error && (
              <p role="alert" className="library-error">
                {error}
              </p>
            )}
          </main>
        </div>
      )}
      {modal === 'handoff' && <HandoffDialog detail={detail} onClose={() => setModal(null)} />}
      {(modal === 'import' || modal === 'legacy') && (
        <ImportDialog
          id={id}
          kind={modal === 'import' ? 'minutes' : 'transcript'}
          onClose={() => setModal(null)}
          onDone={imported}
        />
      )}
      {modal === 'sources' && (
        <Sources
          detail={detail}
          working={starting || transcribing}
          onStart={startASR}
          onClose={() => setModal(null)}
          onLegacy={() => setModal('legacy')}
        />
      )}
    </>
  );
}
function App() {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const changed = () => setHash(window.location.hash);
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  const match = /^#\/recordings\/([a-zA-Z0-9_-]+)$/.exec(hash);
  return match ? (
    <RecordPage key={match[1]} id={match[1]} />
  ) : (
    <Library key={hash} start={!hash || hash === '#/'} />
  );
}
createRoot(document.getElementById('root')!).render(<App />);
