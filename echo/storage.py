"""Portable library manifests and presentation data; private content stays on disk."""
import json
import hashlib
import os
import fcntl
from functools import wraps
from pathlib import Path
from contextlib import closing

from echo import paths, library, workspace_review


def serial_write(function):
    @wraps(function)
    def wrapped(data, *args, **kwargs):
        paths.ensure_data_directory(data)
        with (Path(data) / 'library-state.lock').open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                return function(data, *args, **kwargs)
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    return wrapped


def root(data):
    value = paths._config(data).get('library_root')
    return paths._resolve(value, data) if value else None


def contained(base, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError('资料路径必须相对资料库保存')
    target = (Path(base) / relative).resolve()
    if not target.is_relative_to(Path(base).resolve()):
        raise ValueError('资料路径超出资料库')
    return target


def manifest(data):
    base = root(data)
    if base is None:
        return None
    value = library.read_store(base, 'library.json')
    if value.get('schema_version') != 1 or not isinstance(value.get('sessions'), dict):
        raise ValueError('资料库清单缺失或无效，请恢复原 library.json')
    return value


def session(data, key):
    book = manifest(data)
    if book is None or key not in book['sessions']:
        return None
    folder = contained(root(data), book['sessions'][key])
    value = library.read_store(folder, 'interview.json')
    if value.get('id') != key:
        raise ValueError('场次清单身份不匹配，请核对 interview.json')
    return folder, value


def selected_run(data, key):
    entry = session(data, key)
    return entry[1].get('preferred_run_id') if entry else library.read_store(data, 'library.json').get('interviews', {}).get(key, {}).get('preferred_run_id')


@serial_write
def select_run(data, key, run_id):
    entry = session(data, key)
    if entry:
        folder, value = entry
        value['preferred_run_id'] = run_id
        library.write_store(folder, 'interview.json', value)
    else:
        library.update_metadata(data, key, preferred_run_id=run_id)


def review(data, key):
    entry = session(data, key)
    if entry:
        folder, value = entry
        return contained(folder, value.get('review', '复盘.md'))
    return None


def transcription_directory(data, key, run_id):
    entry = session(data, key)
    return entry[0] / 'transcripts' / run_id if entry else None


def scoped_key(data):
    book = manifest(data)
    return book['id'] if book else str(paths.source_directory(data, required=False) or '')


def navigation(data):
    return library.read_store(data, 'navigation.json').get(scoped_key(data), {})


@serial_write
def save_navigation(data, value):
    saved = library.read_store(data, 'navigation.json')
    saved[scoped_key(data)] = value
    library.write_store(data, 'navigation.json', saved)


def documents(data, key=None):
    base = root(data) or paths.output_directory(data)
    entries = []
    if key:
        entry = session(data, key)
        if not entry:
            return []
        folder, info = entry
        candidates = [contained(folder, rel) for rel in info.get('documents', [])]
    else:
        candidates = sorted((base / 'reports').glob('*.md'))
        if (base / '面试总结.md').is_file():
            candidates.append(base / '面试总结.md')
        # Memory stays bound to its actual source, even when outputs change.
        from echo import memory
        try:
            state = memory.status(data)
            if state['initialized']:
                memory_root = Path(state['root'])
                candidates += [memory_root / 'MEMORY.md', memory_root / 'profile.md']
                candidates += sorted((memory_root / 'topics').glob('*.md'))
        except memory.MemoryStorageError:
            pass
    for target in candidates:
        if target.is_file() and target.stat().st_size <= 2_000_000:
            entries.append({'id': hashlib.sha256(str(target).encode()).hexdigest()[:20], 'title': target.stem, 'path': str(target)})
    return entries


def read_material(data, document_id, key=None):
    item = next((item for item in documents(data, key) if item['id'] == document_id), None)
    if item is None:
        raise ValueError('资料已变化，请刷新列表')
    return {**item, 'body': Path(item['path']).read_text(encoding='utf-8')}


def overview(data, items):
    from echo import memory
    result = []
    for item in items:
        info = dict(item)
        target = review(data, item['id'])
        if target:
            doc = workspace_review.read_document(target, kind='interview', interview_id=item['id'])
            info['review_status'] = doc['metadata'].get('status', '已有文档') if doc else '待复盘'
            info['actions'] = workspace_review.action_items(doc['body']) if doc else []
        else:
            info['review_status'], info['actions'] = '待核对', []
        info['preferred_run_id'] = selected_run(data, item['id'])
        info['documents'] = documents(data, item['id'])
        result.append(info)
    try:
        memory_state = memory.status(data)
    except memory.MemoryStorageError as exc:
        memory_state = {'initialized': False, 'warnings': [str(exc)]}
    return {'items': result, 'documents': documents(data), 'navigation': navigation(data),
            'memory': memory_state, 'managed': root(data) is not None,
            'storage': str(root(data) or paths.output_directory(data))}


@serial_write
def sync_sessions(data, items):
    """Register newly scanned media without moving existing sessions on rename."""
    book = manifest(data)
    if book is None:
        return
    base = root(data)
    for item in items:
        if not paths.media_in_source(data, item['path']):
            continue
        if item['id'] not in book['sessions']:
            info = library.inferred_metadata(item)
            folder = Path('interviews') / info['company'] / item['id']
            target = base / folder
            target.mkdir(parents=True, exist_ok=True)
            library.write_store(target, 'interview.json', {'schema_version': 1, 'id': item['id'],
                'source': os.path.relpath(item['path'], base), 'source_sha256': item['sha256'],
                'review': '复盘.md', 'documents': [], 'preferred_run_id': None})
            book['sessions'][item['id']] = str(folder)
        else:
            folder = contained(base, book['sessions'][item['id']])
            value = library.read_store(folder, 'interview.json')
            if value.get('id') != item['id']:
                raise ValueError('场次清单身份不匹配')
            value.update(source=os.path.relpath(item['path'], base), source_sha256=item['sha256'])
            library.write_store(folder, 'interview.json', value)
    library.write_store(base, 'library.json', book)


def relink(data):
    """Explicit offline repair after moving a complete library; IDs never change."""
    import sqlite3
    from datetime import datetime
    from echo import interview as core, queue_jobs
    data = Path(data).resolve()
    previous_data = core.DATA
    core.DATA = data
    try:
        if core.worker_active() or any(job['state'] in {'queued', 'running'} for job in queue_jobs.jobs()):
            raise ValueError('存在活动任务，请先结束或取消任务再重关联')
        book = manifest(data)
        if book is None:
            raise ValueError('此资料库尚未登记')
        memory_root = root(data) / 'memory'
        marker = memory_root / '.echo-memory.json'
        if marker.is_file() and library.read_store(memory_root, '.echo-memory.json').get('library_id') != book['id']:
            raise ValueError('Memory 资料库身份不一致，需人工核对')
        changes = []
        source_root = paths.source_directory(data)
        for key in book['sessions']:
            folder, value = session(data, key)
            media = (root(data) / value['source']).resolve()
            if not media.is_relative_to(source_root) or not paths.media_in_source(data, media):
                raise ValueError('原媒体位置超出当前资料库')
            if not media.is_file() or core.digest(media) != value['source_sha256']:
                raise ValueError(f'原媒体缺失或内容变化，停止重关联：{media.name}')
            changes.append((key, folder, media))
        backup = data / ('ledger-before-relink-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.sqlite3')
        with core.db() as c, closing(sqlite3.connect(backup)) as saved:
            c.backup(saved)
        with core.db() as c:
            for key, folder, media in changes:
                stat = media.stat()
                c.execute('UPDATE interviews SET path=?,size=?,mtime_ns=? WHERE id=?', (str(media), stat.st_size, stat.st_mtime_ns, key))
                for row in c.execute('SELECT id FROM runs WHERE interview_id=?', (key,)).fetchall():
                    target = folder / 'transcripts' / row['id']
                    if (target / 'transcript.json').is_file():
                        c.execute('UPDATE runs SET output_dir=? WHERE id=?', (str(target), row['id']))
        memory_root = root(data) / 'memory'
        marker = memory_root / '.echo-memory.json'
        if marker.is_file():
            owner = library.read_store(memory_root, '.echo-memory.json')
            # Explicitly repairing the same library only; never adopt another ID.
            if owner.get('library_id') != book['id']:
                raise ValueError('Memory 资料库身份不一致，需人工核对')
            owner.update(source_directory=str(source_root), data_directory=str(data))
            library.write_store(memory_root, '.echo-memory.json', owner)
            library.write_store(data, 'memory-locations.json', {'schema_version': 1, 'locations': {str(source_root): str(memory_root)}})
        return {'relinked': len(changes), 'backup': str(backup)}
    finally:
        core.DATA = previous_data


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='重关联已搬迁的资料库，保留身份与笔记')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--relink', action='store_true', required=True)
    args = parser.parse_args()
    print(json.dumps(relink(args.data_dir), ensure_ascii=False, indent=2))
