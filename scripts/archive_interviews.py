"""Build a private, repeatable archive view without relocating source files."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.parse import quote
import uuid

ROOT = Path(__file__).resolve().parents[1]
MEDIA = {'.mov', '.mp4', '.mkv', '.wav', '.mp3', '.m4a', '.webm'}


def write(path, body):
    """Atomically replace generated metadata only."""
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
        f.write(body)
        name = f.name
    os.replace(name, path)


def link(path, target):
    target = target.resolve()
    if path.is_symlink() and path.resolve() == target:
        return
    if path.exists() or path.is_symlink():
        raise ValueError(f'归档入口冲突，保留原文件：{path}')
    path.symlink_to(os.path.relpath(target, path.parent), target_is_directory=target.is_dir())


def md_link(label, target):
    label = str(label).replace('|', '／').replace('[', '（').replace(']', '）').replace('\n', ' ')
    return f'[{label}]({quote(str(target), safe="/.-")})'


def build(source, data, destination):
    source, data, destination = (Path(p).resolve() for p in (source, data, destination))
    if not source.is_dir():
        raise ValueError('录屏目录不存在')
    if destination == source or source in destination.parents:
        raise ValueError('归档目录必须位于录屏目录之外，避免重复扫描')
    if destination == ROOT or ROOT in destination.parents and data not in destination.parents:
        raise ValueError('私人归档应位于代码仓库之外或本地数据目录内')
    ledger = data / 'ledger.sqlite3'
    if not ledger.is_file():
        raise ValueError('未找到 Echo 台账，请先在 Echo 中扫描录屏')
    destination.mkdir(parents=True, exist_ok=True)
    with (destination / '.archive.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _build(source, data, destination, ledger)


def _build(source, data, destination, ledger):
    generation = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    history = destination / '90_归档记录' / generation
    history.mkdir(parents=True)
    # SQLite backup gives a consistent snapshot even while the app is running.
    with closing(sqlite3.connect(ledger.as_uri() + '?mode=ro', uri=True)) as src, closing(sqlite3.connect(history / 'ledger.sqlite3')) as dst:
        src.backup(dst)
    with closing(sqlite3.connect(history / 'ledger.sqlite3')) as db:
        db.row_factory = sqlite3.Row
        interviews = [dict(r) for r in db.execute('SELECT * FROM interviews ORDER BY path')]
        runs = [dict(r) for r in db.execute('SELECT * FROM runs ORDER BY started_at')]
        notes = [dict(r) for r in db.execute('SELECT * FROM notes ORDER BY seq')]
    by_path = {str(Path(r['path']).resolve()): r for r in interviews}
    files = {str(p.resolve()): p.resolve() for p in source.rglob('*')
             if p.is_file() and not p.is_symlink() and p.suffix.lower() in MEDIA}
    # Preserve visibility of ledger entries whose source has gone missing.
    for item in interviews:
        files.setdefault(str(Path(item['path']).resolve()), Path(item['path']).resolve())
    rows = []
    for path, recording in sorted(files.items()):
        item = by_path.get(path)
        key = hashlib.sha256(path.encode()).hexdigest()[:12]
        title = recording.parent.name + ' / ' + recording.stem
        slug = re.sub(r'[^\w\-\u4e00-\u9fff]+', '_', recording.parent.name)[:65]
        folder = destination / '10_逐场面试' / f'{slug}--{key}'
        for sub in ('01_原始资料', '02_转写版本', '03_复盘', '04_练习'):
            (folder / sub).mkdir(parents=True, exist_ok=True)
        if recording.exists():
            link(folder / '01_原始资料' / recording.name, recording)
            for extra in sorted(recording.parent.iterdir()):
                if extra.is_file() and not extra.is_symlink() and extra.suffix.lower() in {'.md', '.txt', '.pdf', '.docx', '.png', '.jpg'}:
                    link(folder / '01_原始资料' / extra.name, extra)
        versions = [r for r in runs if item and r['interview_id'] == item['id']]
        entries = []
        ready = 0
        for run in versions:
            output = Path(run['output_dir']) if run.get('output_dir') else None
            available = bool(output and (output / 'transcript.json').is_file())
            if run['state'] == 'completed' and available:
                ready += 1
                link(folder / '02_转写版本' / run['id'], output)
            entries.append({**run, 'result_available': available})
        status = '待转写' if item else '待 Echo 扫描'
        if ready:
            status = f'已有 {ready} 个可用转写版本（含试转，需核对范围）'
        elif any(r['state'] == 'completed' for r in versions):
            status = '已完成记录的结果文件缺失'
        if not recording.exists():
            status = '源文件缺失；' + status
        session_notes = [n for n in notes if item and n['interview_id'] == item['id']]
        note_text = '# Echo 笔记快照\n\n自动生成；在 Echo 中追加笔记，或在 03_复盘 中写独立文件。\n\n'
        note_text += '\n\n'.join(f"## {n['at']} · #{n['seq']}\n\n{n['body']}" for n in session_notes) or '暂无 Echo 笔记。\n'
        write(folder / '03_复盘' / 'Echo笔记快照.md', note_text)
        template = folder / '03_复盘' / '复盘.md'
        if not template.exists():
            with template.open('x', encoding='utf-8') as f:
                f.write('# 面试复盘\n\n状态：待复盘\n\n## 本场结论\n\n待填写；不要把转写错误当作回答错误。\n\n## 逐题复盘\n\n| 视频时间 | 问题与追问 | 我的实际回答 | 问题与证据 | 改进回答 | 待回听 |\n| --- | --- | --- | --- | --- | --- |\n\n## 做得好的部分\n\n## 优先补强的三件事\n\n## 下次重练与验证\n\n记录练习日期、题目、表现与下一次检查时间。\n')
        row = {'archive_key': key, 'title': title, 'source': path,
               'interview_id': item['id'] if item else None, 'status': status,
               'folder': str(folder.relative_to(destination)), 'runs': entries, 'notes': session_notes,
               'source_size': recording.stat().st_size if recording.exists() else None,
               'ledger_sha256': item['sha256'] if item else None}
        rows.append(row)
        write(folder / '档案.json', json.dumps(row, ensure_ascii=False, indent=2) + '\n')
        write(folder / 'README.md', f'# {title}\n\n{status}\n\n' + '\n\n'.join(md_link(s, s + '/') for s in ('01_原始资料', '02_转写版本', '03_复盘', '04_练习')) + '\n\n原始资料与转写版本是链接，编辑会影响原件。个人复盘写入 03_复盘/复盘.md；此索引与笔记快照会自动更新。\n')
    (destination / '20_跨场总结').mkdir(exist_ok=True)
    summary = destination / '20_跨场总结' / '问题与行动.md'
    if not summary.exists():
        summary.write_text('# 跨场问题与行动\n\n| 主题 | 出现在哪场与时间点 | 共同问题 | 行动 | 验证结果 |\n| --- | --- | --- | --- | --- |\n', encoding='utf-8')
    manifest = {'schema_version': 1, 'generation': generation, 'source': str(source), 'data': str(data), 'sessions': rows}
    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    (history / 'manifest.json').write_text(payload, encoding='utf-8')
    write(destination / 'manifest.json', payload)
    index = '# 面试复盘档案\n\n自动更新的导航；个人复盘与练习文件始终保留。\n\n| 面试 | 资料状态 |\n| --- | --- |\n'
    index += '\n'.join(f"| {md_link(r['title'], r['folder'] + '/README.md')} | {r['status']} |" for r in rows)
    index += '\n\n## 目录职责\n\n- 10_逐场面试：每场的原始资料入口、全部转写版本、个人复盘、练习。\n- 20_跨场总结：重复问题、补强行动与验证。\n- 90_归档记录：每次执行独立保存台账快照和清单，不覆盖历史。\n\n原视频、附件、转写仍在原处，入口使用相对符号链接。这是归档视图，不是完整文件备份。完整恢复需另行备份录屏与运行数据；移动原件会使链接失效。\n\n台账快照包含当时的笔记；不包含此目录内手工撰写的复盘。请将整个档案目录纳入个人备份。归档不触发转写，也不改变任务状态。\n'
    write(destination / 'README.md', index)
    return manifest


def main():
    data = Path(os.environ.get('INTERVIEW_DATA_DIR', ROOT / '.local')).resolve()
    source_file = data / 'source_path.txt'
    archive_file = data / 'archive_path.txt'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=data)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    data = args.data.resolve()
    source_file, archive_file = data / 'source_path.txt', data / 'archive_path.txt'
    source = args.source or Path(os.environ.get('INTERVIEW_SOURCE_DIR') or (source_file.read_text().strip() if source_file.exists() else ROOT / 'recordings'))
    destination = args.destination or Path(archive_file.read_text().strip() if archive_file.exists() else data / 'archives')
    result = build(source, data, destination)
    print(f"已归档 {len(result['sessions'])} 场：{destination.resolve() / 'README.md'}")
    print(f"快照：{result['generation']}")


if __name__ == '__main__':
    main()
