let session: Promise<string> | undefined;

async function readResponse<T>(reply: Response): Promise<T> {
  const data = await reply.json().catch(() => ({}));
  if (!reply.ok) {
    if (reply.status === 401 || reply.status === 403) session = undefined;
    const reason = typeof data.error === 'string' ? data.error : data.message;
    throw new Error(reason || '本机服务返回错误（' + reply.status + '）。');
  }
  return data as T;
}

export function connect(): Promise<string> {
  session ??= fetch('/api/bootstrap', { cache: 'no-store' })
    .then(readResponse<{ csrf: string }>)
    .then((data) => data.csrf)
    .catch((error) => {
      session = undefined;
      throw error;
    });
  return session;
}

export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  await connect();
  return readResponse<T>(await fetch(path, { cache: 'no-store', signal }));
}

export async function send<T>(path: string, value: unknown, filename?: string): Promise<T> {
  const headers: Record<string, string> = { 'X-Echo-CSRF': await connect() };
  let body: BodyInit;
  if (value instanceof File) {
    headers['Content-Type'] = 'application/octet-stream';
    headers['X-Echo-Filename'] = encodeURIComponent(filename || value.name);
    body = value;
  } else {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(value);
  }
  return readResponse<T>(await fetch(path, { method: 'POST', headers, body }));
}

export function recordingPath(id: string) {
  return '/api/recordings/' + encodeURIComponent(id);
}

export function recordingUrl(id: string) {
  return '#/recordings/' + encodeURIComponent(id);
}

export function timecode(seconds: number) {
  const all = Math.floor(Math.max(0, seconds));
  const minutes = Math.floor(all / 60);
  return minutes + ':' + String(all % 60).padStart(2, '0');
}

export function explain(error: unknown) {
  return error instanceof Error ? error.message : '操作没有完成，请重试。';
}

export async function readJSON(file: File) {
  try {
    return JSON.parse(await file.text());
  } catch {
    throw new Error('无法读取 JSON。请检查所选文件是否完整，再重新选择。');
  }
}
