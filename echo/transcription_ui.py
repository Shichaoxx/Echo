"""Presentation helpers for live transcription settings."""
import json
import subprocess
from functools import lru_cache


def scope_label(options):
    start, limit = options.get('start', 0), options.get('limit', 0)
    if limit:
        return f'{start}–{start + limit} 秒 · {limit} 秒片段'
    return f'{start} 秒至录屏结束' if start else '完整录屏'


@lru_cache(maxsize=128)
def media_duration(path, size, mtime_ns):
    try:
        value = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                '-of', 'json', path], capture_output=True, text=True, timeout=8, check=True)
        duration = float(json.loads(value.stdout)['format']['duration'])
        return duration if duration > 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        return None


def range_error(start, limit, duration):
    if duration is not None and start >= duration:
        return '起始位置已超出录屏时长，请调整。'
    if duration is not None and limit and start + limit > duration + 0.5:
        return '片段结束位置超出录屏时长，请缩短片段。'
    return None
