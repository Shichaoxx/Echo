import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { explain, get, readJSON, recordingPath, send, timecode } from './api';
import { collectInterviewMedia } from './folderImport';
import type { Detail, ImportResult, Recording, Run } from './types';

export function Dialog({
  title,
  children,
  onClose,
  busy = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  busy?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const previous = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="echo-dialog echo-glass"
      aria-labelledby="dialog-heading"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <header>
        <h2 id="dialog-heading">{title}</h2>
        <button
          className="close-dialog echo-press"
          type="button"
          aria-label="关闭"
          disabled={busy}
          onClick={onClose}
        >
          ×
        </button>
      </header>
      {children}
    </dialog>
  );
}

type FolderEntry = ReturnType<typeof collectInterviewMedia>[number] & {
  selected: boolean;
  status: 'ready' | 'uploading' | 'done' | 'failed';
  error?: string;
};

function FolderPreview({
  entries,
  busy,
  onSelect,
}: {
  entries: FolderEntry[];
  busy: boolean;
  onSelect: (path: string, selected: boolean) => void;
}) {
  if (!entries.length) {
    return <p role="status">没有找到可导入的录音或视频。请重新选择包含面试媒体的文件夹。</p>;
  }
  const imported = entries.filter((entry) => entry.status === 'done').length;
  return (
    <>
      <p role="status">找到 {entries.length} 个媒体文件，已导入 {imported} 个。</p>
      <ul className="folder-import-list" aria-label="待核对的录音">
        {entries.map((entry) => (
          <li key={entry.path}>
            <label>
              <input
                type="checkbox"
                aria-label={entry.title}
                checked={entry.selected}
                disabled={busy || entry.status === 'done'}
                onChange={(e) => onSelect(entry.path, e.target.checked)}
              />
              <span>
                <strong>{entry.title}</strong>
                {entry.date && <time dateTime={entry.date}>{entry.date}</time>}
                <small>{entry.path}</small>
                {entry.status === 'uploading' && <small role="status">正在导入…</small>}
                {entry.status === 'done' && <small className="import-success">已导入</small>}
                {entry.status === 'failed' && <small role="alert">导入失败 · {entry.error}</small>}
              </span>
            </label>
          </li>
        ))}
      </ul>
    </>
  );
}

function uploadRecording(file: File, title: string, date: string) {
  const params = new URLSearchParams({ title: title.trim(), date });
  return send<{ recording: Recording }>('/api/recordings?' + params, file);
}

export function UploadDialog({
  onClose,
  onCreated,
  onFolderImported,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
  onFolderImported: () => void;
}) {
  const [media, setMedia] = useState<File>();
  const mediaInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const [entries, setEntries] = useState<FolderEntry[] | null>(null);
  const [title, setTitle] = useState(''),
    [date, setDate] = useState('');
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!media || busy) return;
    setBusy(true);
    setError('');
    try {
      const data = await uploadRecording(media, title, date);
      onCreated(data.recording.id);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }
  function updateEntry(path: string, changes: Partial<FolderEntry>) {
    setEntries((current) =>
      current?.map((entry) => (entry.path === path ? { ...entry, ...changes } : entry)) ?? null,
    );
  }
  const pending = entries?.filter((entry) => entry.selected && entry.status !== 'done') ?? [];
  const folderAction = pending.some((entry) => entry.status === 'failed') ? '重试所选' : '导入所选';
  async function importFolder(event: FormEvent) {
    event.preventDefault();
    if (busy || !pending.length) return;
    setBusy(true);
    let imported = 0;
    try {
      for (const entry of pending) {
        updateEntry(entry.path, { status: 'uploading', error: undefined });
        try {
          await uploadRecording(entry.file, entry.title, entry.date);
          updateEntry(entry.path, { status: 'done' });
          imported++;
        } catch (err) {
          updateEntry(entry.path, { status: 'failed', error: explain(err) });
        }
      }
      if (imported > 0) onFolderImported();
      if (imported === pending.length) onClose();
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog title="导入录音" onClose={onClose} busy={busy}>
      <form onSubmit={entries ? importFolder : submit}>
        <label>
          录音或视频
          <input
            ref={mediaInput}
            required={!entries}
            type="file"
            accept="audio/*,video/*,.m4a,.mkv,.wav"
            disabled={busy}
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              setEntries(null);
              setError('');
              if (folderInput.current) folderInput.current.value = '';
              setMedia(file);
              setTitle(file.name.replace(/\.[^.]+$/, ''));
            }}
          />
        </label>
        <label>
          选择面试文件夹
          <input
            ref={folderInput}
            {...{ webkitdirectory: '' }}
            type="file"
            multiple
            aria-describedby="folder-import-hint"
            disabled={busy}
            onChange={(event) => {
              const files = event.currentTarget.files;
              if (!files) return;
              const matches = collectInterviewMedia(files);
              setEntries(matches.map((entry) => ({ ...entry, selected: true, status: 'ready' })));
              setMedia(undefined);
              setError('');
              setTitle('');
              setDate('');
              if (mediaInput.current) mediaInput.current.value = '';
            }}
          />
        </label>
        <p className="folder-import-hint" id="folder-import-hint">
          可选择一场面试的文件夹，也可选择包含多场面试的上级文件夹。选择后可核对录音。
        </p>
        {entries ? (
          <>
            <FolderPreview
              entries={entries}
              busy={busy}
              onSelect={(path, selected) => updateEntry(path, { selected })}
            />
            {entries.length > 0 && (
              <footer>
                <button className="lib-primary echo-press" disabled={busy || !pending.length}>
                  {busy ? '正在导入…' : folderAction + '（' + pending.length + '）'}
                </button>
              </footer>
            )}
          </>
        ) : (
          <>
            <label>
              面试标题
              <input
                required
                value={title}
                maxLength={200}
                disabled={busy}
                onChange={(e) => setTitle(e.target.value)}
              />
            </label>
            <label>
              面试日期（可选）
              <input type="date" value={date} disabled={busy} onChange={(e) => setDate(e.target.value)} />
            </label>
            {error && (
              <p className="library-error" role="alert">
                {error}
              </p>
            )}
            <footer>
              <button className="lib-primary echo-press" disabled={busy || !media || !title.trim()}>
                {busy ? '正在导入…' : '导入并继续'}
              </button>
            </footer>
          </>
        )}
      </form>
    </Dialog>
  );
}

export function runLabel(run: Run) {
  const kind = { full: '整场转写', partial: '片段转写', unknown: '转写记录' }[run.scope.kind];
  const stamp = new Date(run.created_at).toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
  return kind + ' · ' + timecode(run.scope.start) + '–' + timecode(run.scope.end) + ' · ' + stamp;
}

export function HandoffDialog({ detail, onClose }: { detail: Detail; onClose: () => void }) {
  const [run, setRun] = useState(detail.document?.source_run_id || detail.runs[0]?.id || '');
  const [prompt, setPrompt] = useState(''),
    [error, setError] = useState(''),
    [note, setNote] = useState('');
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    setPrompt('');
    setNote('');
    setError('');
    if (run)
      get<{ prompt: string }>(
        recordingPath(detail.recording.id) + '/handoff?run=' + encodeURIComponent(run),
        controller.signal,
      )
        .then((data) => {
          if (!controller.signal.aborted) setPrompt(data.prompt);
        })
        .catch((err) => {
          if (!controller.signal.aborted) setError(explain(err));
        });
    return () => controller.abort();
  }, [detail.recording.id, run]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(prompt);
      setNote('已复制，可粘贴给 Codex。');
    } catch {
      area.current?.focus();
      area.current?.select();
      setNote('交接文字已选中，可手动复制。');
    }
  }
  return (
    <Dialog title="交给 Codex 生成" onClose={onClose}>
      <p>将交接文字发给 Codex，把返回的 JSON 文件导回本场面试。</p>
      <label>
        采用哪次转写
        <select value={run} onChange={(e) => setRun(e.target.value)}>
          {detail.runs.map((r) => (
            <option key={r.id} value={r.id}>
              {runLabel(r)}
            </option>
          ))}
        </select>
      </label>
      <label>
        交接内容
        <textarea ref={area} readOnly rows={8} value={prompt} placeholder="正在读取逐字稿…" />
      </label>
      {error && (
        <p className="library-error" role="alert">
          {error}
        </p>
      )}
      <p role="status">{note}</p>
      <footer>
        <button
          className="echo-press"
          disabled={!prompt}
          type="button"
          onClick={() => {
            const url = URL.createObjectURL(new Blob([prompt], { type: 'text/plain;charset=utf-8' }));
            const a = document.createElement('a');
            a.href = url;
            a.download = 'echo-minutes-handoff.txt';
            a.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          }}
        >
          下载交接文字
        </button>
        <button className="lib-primary echo-press" disabled={!prompt} onClick={copy}>
          复制给 Codex
        </button>
      </footer>
    </Dialog>
  );
}

export function ImportDialog({
  kind,
  id,
  onClose,
  onDone,
}: {
  kind: 'minutes' | 'transcript';
  id: string;
  onClose: () => void;
  onDone: (result: ImportResult) => void;
}) {
  const [file, setFile] = useState<File>(),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const minutes = kind === 'minutes';
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file || busy) return;
    setError('');
    setBusy(true);
    try {
      const json = await readJSON(file);
      const result = await send<ImportResult>(
        recordingPath(id) + (minutes ? '/minutes/import' : '/transcripts/import'),
        json,
      );
      onDone(result);
    } catch (err) {
      setError(explain(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog title={minutes ? '导入 Codex 纪要' : '导入旧转写'} onClose={onClose} busy={busy}>
      <form onSubmit={submit}>
        <p>
          {minutes
            ? '选择 Codex 为本场面试生成的纪要文件。Echo 会核对引用，并保存为新版本。'
            : '选择这场录音对应的旧版转写 JSON。Echo 会读取其中的原话和时间，保留原文件。'}
        </p>
        <label>
          {minutes ? '纪要 JSON' : '转写 JSON'}
          <input
            type="file"
            required
            accept=".json,application/json"
            disabled={busy}
            onChange={(e) => setFile(e.target.files?.[0])}
          />
        </label>
        {error && (
          <p className="library-error" role="alert">
            {error}
          </p>
        )}
        <footer>
          <button className="lib-primary echo-press" disabled={!file || busy}>
            {busy ? '正在核对…' : '确认导入'}
          </button>
        </footer>
      </form>
    </Dialog>
  );
}
