"""Explicit offline migration. Preview first; preserve verified backup and stable IDs."""
import argparse
from collections import defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
from contextlib import closing
import re
import shutil
import sqlite3
import uuid

from echo import interview as core, library, paths, workspace_review

GENERATED = ('transcript.json', 'transcript.txt', 'transcript.srt', 'review.md', 'review.json', 'audio.mp3')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def plan(source, legacy_data=None):
    source = Path(source).resolve()
    output = source / 'echo'
    if (output / 'library.json').exists():
        raise ValueError('资料库已登记，不重复迁移；请使用现有 .state')
    old = []
    if legacy_data:
        db = Path(legacy_data) / 'ledger.sqlite3'
        with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as c:
            c.row_factory = sqlite3.Row
            old = [dict(row) for row in c.execute('SELECT * FROM interviews')]
    if any(p.is_symlink() for p in output.rglob('*')):
        raise ValueError('结果目录含符号链接，请先核对真实归属')
    originals = [p for p in source.rglob('*') if p.is_file() and not p.is_symlink()
                 and p.suffix.lower() in core.MEDIA and not p.is_relative_to(output)]
    by_hash = defaultdict(list)
    for media in originals:
        by_hash[core.digest(media)].append(media)
    grouped = defaultdict(list)
    for transcript in sorted(output.rglob('transcript.json')):
        value = read(transcript)
        sha = value.get('source_sha256')
        if not sha or len(by_hash[sha]) != 1:
            raise ValueError(f'转写无法唯一关联原媒体：{transcript}')
        grouped[sha].append((transcript, value))
    sessions = []
    used_folders = set()
    used_ids = set()
    for sha, media_list in by_hash.items():
        if len(media_list) != 1:
            raise ValueError('存在内容相同的多个原媒体，请先明确场次关联')
        media = media_list[0]
        outputs = grouped.get(sha, [])
        folders = {p.parent.parent.parent if p.parent.parent.name == 'versions' else p.parent for p, _ in outputs}
        if len(folders) > 1:
            raise ValueError('同一媒体有多个结果根，请先核对后合并')
        folder = next(iter(folders), None)
        if folder in used_folders:
            raise ValueError('多个媒体共用结果目录，请先明确场次拆分')
        if folder:
            used_folders.add(folder)
        old_match = [row for row in old if row['sha256'] == sha]
        if len(old_match) > 1:
            raise ValueError('旧台账包含重复内容，无法自动选择身份')
        doc_ids = set()
        if folder:
            for doc in folder.glob('*.md'):
                if doc.name == 'review.md':
                    continue
                match = workspace_review.MARKER.match(doc.read_text(encoding='utf-8'))
                if match:
                    value = json.loads(match[1])
                    if value.get('interview_id'):
                        doc_ids.add(value['interview_id'])
        if len(doc_ids) > 1:
            raise ValueError('复盘文件包含不同场次 ID')
        key = old_match[0]['id'] if old_match else next(iter(doc_ids), uuid.uuid4().hex[:12])
        aliases = sorted(doc_ids - {key})
        if key in used_ids or not re.fullmatch(r'[a-zA-Z0-9_-]+', key):
            raise ValueError('场次 ID 重复或格式错误')
        used_ids.add(key)
        title = media.parent.name + ' / ' + media.stem
        metadata = library.inferred_metadata({'title': title})
        date_match = re.search(r'\d{4}-\d{2}-\d{2}', media.name)
        if date_match:
            metadata['date'] = date_match[0]
        label = '_'.join([metadata['date'] or '日期待确认', metadata['round'], key[:6]])
        relative = Path('interviews') / metadata['company'] / label
        runs = []
        for transcript, value in outputs:
            run_id = value.get('run_id')
            if not isinstance(run_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', run_id):
                raise ValueError('转写版本缺少有效 run_id')
            runs.append({'id': run_id, 'old': str(transcript.parent), 'metadata': value})
        sessions.append({'id': key, 'source': str(media), 'sha256': sha, 'title': title,
                         'metadata': metadata, 'legacy_ids': aliases, 'old': str(folder) if folder else None,
                         'folder': str(relative), 'runs': runs})
    # Validate every restored version against the old ledger before any move.
    if legacy_data:
        with closing(sqlite3.connect((Path(legacy_data).resolve() / 'ledger.sqlite3').as_uri() + '?mode=ro', uri=True)) as c:
            old_runs = dict(c.execute('SELECT id,interview_id FROM runs'))
            for item in sessions:
                for run in item['runs']:
                    if run['id'] in old_runs and old_runs[run['id']] != item['id']:
                        raise ValueError('历史版本归属冲突')
    ids = [run['id'] for item in sessions for run in item['runs']]
    if len(ids) != len(set(ids)):
        raise ValueError('转写版本 ID 重复')
    return {'source': str(source), 'output': str(output), 'sessions': sessions,
            'legacy_data': str(Path(legacy_data).resolve()) if legacy_data else None}


def fingerprint(root):
    return {str(p.relative_to(root)): core.digest(p) for p in Path(root).rglob('*') if p.is_file()}


def _apply(value, backup):
    source, output = Path(value['source']), Path(value['output'])
    if (output / '.state').exists() or (output / 'library.json').exists():
        raise ValueError('目标已有状态，拒绝覆盖；请恢复或检查此前迁移')
    # A migration is offline; never copy an active queue into a new workspace.
    legacy = Path(value['legacy_data']) if value['legacy_data'] else None
    if legacy:
        import fcntl
        for name in ('worker.lock', 'queue.lock'):
            lock = legacy / name
            if lock.exists():
                with lock.open('r') as f:
                    try:
                        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise ValueError('旧资料库仍有活动任务，请停止任务后迁移') from None
    backup.mkdir()
    if output.exists():
        shutil.copytree(output, backup / 'echo')
        if fingerprint(output) != fingerprint(backup / 'echo'):
            raise ValueError('备份校验失败，原资料未移动')
    library.write_store(backup, 'plan.json', value)
    (backup / 'verified').write_text('Output backup verified before migration.\n')
    data = output / '.state'
    data.mkdir(parents=True)
    if legacy:
        with closing(sqlite3.connect((legacy / 'ledger.sqlite3').as_uri() + '?mode=ro', uri=True)) as old_db:
            with closing(sqlite3.connect(backup / 'ledger.sqlite3')) as backup_db:
                old_db.backup(backup_db)
        shutil.copy2(backup / 'ledger.sqlite3', data / 'ledger.sqlite3')
        for name in ('library.json', 'memory-locations.json'):
            if (legacy / name).is_file():
                shutil.copy2(legacy / name, data / name)
    core.DATA = data
    paths.save_directories(data, source, output, default_output=True, output_layout='company')
    config = paths._config(data)
    config.update(library_root='..', source='../..', output='..')
    library.write_store(data, 'paths.json', config)
    book = {'schema_version': 1, 'id': uuid.uuid4().hex, 'sessions': {}}
    remap = {}
    for item in value['sessions']:
        target = output / item['folder']
        target.parent.mkdir(parents=True, exist_ok=True)
        old = Path(item['old']) if item['old'] else None
        if old:
            for file in old.rglob('*'):
                if file.is_file():
                    remap[file.resolve()] = target / file.relative_to(old)
            shutil.move(str(old), target)
        else:
            target.mkdir()
        for run in item['runs']:
            previous = Path(run['old'])
            from_dir = target / previous.relative_to(old)
            to_dir = target / 'transcripts' / run['id']
            to_dir.mkdir(parents=True)
            for name in GENERATED:
                if (from_dir / name).is_file():
                    shutil.move(str(from_dir / name), to_dir / name)
                    remap[previous / name] = to_dir / name
            if from_dir != target and from_dir.is_dir() and not any(from_dir.iterdir()):
                from_dir.rmdir()
            meta = run['metadata']
            with core.db() as c:
                existing = c.execute('SELECT id FROM interviews WHERE id=?', (item['id'],)).fetchone()
                media = Path(item['source']); stat = media.stat()
                if existing:
                    c.execute('UPDATE interviews SET path=?,title=?,size=?,mtime_ns=?,sha256=? WHERE id=?',
                              (str(media), item['title'], stat.st_size, stat.st_mtime_ns, item['sha256'], item['id']))
                else:
                    c.execute('INSERT INTO interviews VALUES(?,?,?,?,?,?,?,?)',
                              (item['id'], str(media), item['title'], item['sha256'], stat.st_size, stat.st_mtime_ns, core.now(), core.now()))
                prior = c.execute('SELECT interview_id FROM runs WHERE id=?', (run['id'],)).fetchone()
                if prior and prior['interview_id'] != item['id']:
                    raise ValueError('台账转写归属冲突；请从备份恢复')
                c.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET output_dir=excluded.output_dir',
                          (run['id'], item['id'], meta['source_sha256'], meta.get('model', 'unknown'),
                           json.dumps(meta.get('options', {})), 'completed', meta.get('created_at', core.now()),
                           meta.get('created_at', core.now()), str(to_dir), None))
        if not item['runs']:
            media = Path(item['source']); stat = media.stat()
            with core.db() as c:
                c.execute('INSERT INTO interviews VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path',
                          (item['id'], str(media), item['title'], item['sha256'], stat.st_size, stat.st_mtime_ns, core.now(), core.now()))
        # Same unique source hash proves identity; retain historical IDs as aliases.
        for document in target.glob('*.md'):
            body = document.read_text(encoding='utf-8')
            marker = workspace_review.MARKER.match(body)
            if marker:
                metadata = json.loads(marker[1])
                if metadata.get('interview_id') in item.get('legacy_ids', []):
                    metadata['legacy_interview_ids'] = sorted(set(metadata.get('legacy_interview_ids', []) + [metadata['interview_id']]))
                    metadata['interview_id'] = item['id']
                    document.write_text('<!-- echo-review\n' + json.dumps(metadata, ensure_ascii=False, indent=2) + '\n-->\n\n' + body[marker.end():], encoding='utf-8')
        canonical = target / '复盘.md'
        candidates = [p for p in target.glob('*.md') if p.name != 'review.md']
        if not canonical.exists() and len(candidates) == 1:
            canonical = candidates[0]
        preferred = None
        if canonical.is_file():
            doc = workspace_review.read_document(canonical, kind='interview', interview_id=item['id'])
            preferred = next((run for run in doc['metadata'].get('source_run_ids', []) if run in {r['id'] for r in item['runs']}), None)
        if not preferred and item['runs']:
            ordered = sorted(item['runs'], key=lambda r: (not bool(r['metadata'].get('options', {}).get('limit')), r['metadata'].get('created_at', '')), reverse=True)
            preferred = ordered[0]['id']
        session = {'schema_version': 1, 'id': item['id'], 'source': os.path.relpath(item['source'], output),
                   'source_sha256': item['sha256'], 'legacy_ids': item.get('legacy_ids', []), 'review': canonical.name, 'preferred_run_id': preferred,
                   'documents': [str(p.relative_to(target)) for p in candidates if p != canonical]}
        library.write_store(target, 'interview.json', session)
        library.update_metadata(data, item['id'], **item['metadata'])
        book['sessions'][item['id']] = item['folder']
        versions = target / 'versions'
        if versions.is_dir() and not any(versions.iterdir()):
            versions.rmdir()
    # Rebase existing Markdown links by actual old target, preserving unresolved evidence.
    repairs = 0
    for before, after in remap.items():
        if after.suffix != '.md' or not after.exists():
            continue
        def link(match):
            nonlocal repairs
            raw = match[1]; angled = raw.startswith('<') and raw.endswith('>')
            path = raw[1:-1] if angled else raw
            if '://' in path or path.startswith('#'):
                return match[0]
            fragment = path.split('#', 1)
            old_target = (before.parent / fragment[0]).resolve()
            new_target = remap.get(old_target, old_target)
            if not new_target.exists():
                return match[0]
            relative = os.path.relpath(new_target, after.parent) + ('#' + fragment[1] if len(fragment) > 1 else '')
            repairs += 1
            return '](' + ('<' + relative + '>' if angled or ' ' in relative else relative) + ')'
        text = after.read_text(encoding='utf-8')
        after.write_text(re.sub(r'\]\((<[^>]+>|[^)]+)\)', link, text), encoding='utf-8')
    library.write_store(output, 'library.json', book)
    memory = output / 'memory'
    if (memory / '.echo-memory.json').is_file():
        # Explicit migration rebinds location metadata only, never memory prose.
        library.write_store(memory, '.echo-memory.json', {'schema_version': 1, 'source_directory': str(source), 'data_directory': str(data), 'library_id': book['id']})
        library.write_store(data, 'memory-locations.json', {'schema_version': 1, 'locations': {str(source): str(memory)}})
    report = {'backup': str(backup), 'sessions': len(value['sessions']),
              'runs': sum(len(s['runs']) for s in value['sessions']), 'repaired_links': repairs,
              'moves': {str(a): str(b) for a, b in remap.items()}}
    library.write_store(data, 'migration.json', report)
    return report


def apply(value):
    source, output = Path(value['source']), Path(value['output'])
    backup = source.parent / ('.echo-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    try:
        return _apply(value, backup)
    except Exception:
        if (backup / 'verified').exists():
            if output.exists():
                output.rename(backup / 'incomplete-migration')
            if (backup / 'echo').is_dir():
                shutil.copytree(backup / 'echo', output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--legacy-data', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    value = plan(args.source, args.legacy_data)
    print(json.dumps(apply(value) if args.apply else {'sessions': [{k: s[k] for k in ('id', 'folder', 'source')} for s in value['sessions']],
                      'runs': sum(len(s['runs']) for s in value['sessions'])}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
