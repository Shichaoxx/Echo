"""Read the single Markdown source maintained by Codex, without executing content."""
import json
from pathlib import Path
import re
from datetime import datetime

MARKER = re.compile(r'\A\s*<!-- echo-review\s*\n(.*?)\n-->\s*', re.S)
STATUSES = {'待复盘', '初稿', '待回听', '已复盘', '已验证'}


def read_document(path, *, kind, interview_id=None):
    path = Path(path)
    if not path.is_file():
        return None
    if path.stat().st_size > 2_000_000:
        return {'body': '', 'metadata': {}, 'warnings': ['文档超过 2 MB，请拆分历史记录后重试。']}
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as exc:
        return {'body': '', 'metadata': {}, 'warnings': [f'无法读取文档：{exc}']}
    match = MARKER.match(text)
    warnings, meta = [], {}
    if match:
        try:
            meta = json.loads(match[1])
            if not isinstance(meta, dict):
                raise ValueError('元数据必须是 JSON 对象')
            if meta.get('schema_version') != 1 or meta.get('kind') != kind:
                warnings.append('文档版本或类型与约定不一致。')
            if interview_id and meta.get('interview_id') != interview_id:
                warnings.append('文档的 interview_id 与当前录屏不一致，请核对来源。')
            if meta.get('status') not in STATUSES:
                warnings.append('复盘状态不符合约定。')
            if not isinstance(meta.get('source_run_ids'), list) or not all(isinstance(x, str) for x in meta.get('source_run_ids', [])):
                warnings.append('source_run_ids 应为转写版本 ID 列表。')
            datetime.fromisoformat(meta.get('updated_at', ''))
        except (ValueError, TypeError):
            warnings.append('元数据或更新时间格式不正确；正文仍可阅读。')
            meta = {}
    else:
        warnings.append('这是普通 Markdown，尚未添加统一复盘元数据。')
    return {'body': text[match.end():] if match else text, 'metadata': meta, 'warnings': warnings}


def review_path(recording):
    path = Path(recording)
    media = {'.mov', '.mp4', '.mkv', '.wav', '.mp3', '.m4a', '.webm'}
    # Multiple recordings in one folder must not share another interview's review.
    specific = path.with_name(path.stem + '.复盘.md')
    if specific.exists() or (path.parent.is_dir() and sum(p.is_file() and p.suffix.lower() in media for p in path.parent.iterdir()) > 1):
        return specific
    return path.parent / '复盘.md'


def template(item, runs):
    meta = {'schema_version': 1, 'kind': 'interview', 'interview_id': item['id'],
            'status': '待复盘', 'updated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
            'source_run_ids': [r['id'] for r in runs if r['state'] == 'completed']}
    return '<!-- echo-review\n' + json.dumps(meta, ensure_ascii=False, indent=2) + '\n-->\n\n# 面试复盘\n\n## 本场结论\n\n待 Codex 阅读来源后填写。\n\n## 来源与覆盖范围\n\n列出实际采用的转写版本、覆盖时间、原始笔记，以及已回听和未回听的范围。元数据中的候选版本需要按实际采用情况删选。\n\n## 逐题复盘\n\n### Q01 · 问题名称 · HH:MM:SS–HH:MM:SS\n\n- 考察点与追问：\n- 实际回答（事实）：\n- 评价与证据（分析）：\n- 改进回答（建议，不冒充实际经历）：\n- 待回听与不确定项：\n\n## 做得好的部分\n\n## 优先补强与练习\n\n| 行动 ID | 优先级 | 问题与证据 | 练习任务 | 验收标准 | 状态 |\n| --- | --- | --- | --- | --- | --- |\n\n## 追加记录\n\n按日期追加回听纠正、重练结果及结论修订，引用原问题 ID；保留原结论。\n'


def action_items(body):
    """Read action tables; later append records with the same ID supersede display only."""
    actions, headers = {}, None
    aliases = {'待做': '待练习', '进行中': '待练习', '待回听': '待回听', '待练习': '待练习', '已验证': '已验证'}
    for line in body.splitlines():
        if not line.strip().startswith('|'):
            headers = None
            continue
        cells = [c.strip().replace('\\|', '|') for c in re.split(r'(?<!\\)\|', line.strip())[1:-1]]
        if '行动 ID' in cells and '状态' in cells:
            headers = cells
            continue
        if not headers or len(cells) != len(headers) or all(re.fullmatch(r'[-: ]*', c) for c in cells):
            continue
        row = dict(zip(headers, cells))
        if row['行动 ID'] and row['状态'] in aliases:
            row['状态'] = aliases[row['状态']]
            actions[row['行动 ID']] = row
    return list(actions.values())
