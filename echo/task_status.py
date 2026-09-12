"""Presentation helpers for truthful, observed transcription progress."""
from datetime import datetime


def elapsed_seconds(started_at, now):
    return max(0, (now - datetime.fromisoformat(started_at)).total_seconds())


def duration_label(seconds):
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes:02} 分" if hours else f"{minutes:02}:{seconds:02}"


def estimate_remaining(anchor, done, total, now_seconds):
    """Estimate from observed chunk throughput, excluding download/extraction."""
    if not anchor or total <= done or done - anchor[0] < 3:
        return None
    elapsed = now_seconds - anchor[1]
    if elapsed < 5:
        return None
    return elapsed / (done - anchor[0]) * (total - done)
