"""Durable FIFO submissions; one dispatcher and one GPU worker at a time."""
import fcntl
import json
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from echo import interview as core
from echo import paths


@contextmanager
def db():
    with core.db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS queue_jobs (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
            interview_id TEXT NOT NULL, source_sha256 TEXT NOT NULL,
            options TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
            finished_at TEXT, run_id TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS queue_preferences (id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL);
        INSERT OR IGNORE INTO queue_preferences VALUES(1,1);
        """)
        yield c


def enabled():
    with db() as c:
        return bool(c.execute("SELECT enabled FROM queue_preferences WHERE id=1").fetchone()[0])


def set_enabled(value):
    with db() as c:
        c.execute("UPDATE queue_preferences SET enabled=? WHERE id=1", (int(value),))


def jobs():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM queue_jobs ORDER BY seq")]


def submit(key, options):
    if options.get("model") not in core.MODELS or options.get("language") not in core.LANGUAGES:
        raise ValueError("请选择支持的模型和语言")
    if options.get("start", 0) < 0 or options.get("limit", 0) < 0 or not 10 <= options.get("chunk_seconds", 25) <= 60:
        raise ValueError("转写范围或分段时长不正确")
    encoded = json.dumps(options, sort_keys=True, ensure_ascii=False)
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        item = c.execute("SELECT * FROM interviews WHERE id=?", (key,)).fetchone()
        if not item:
            raise ValueError("未找到面试")
        prior = c.execute("SELECT id FROM queue_jobs WHERE interview_id=? AND source_sha256=? AND options=? AND state IN ('queued','running')",
                          (key, item["sha256"], encoded)).fetchone()
        if prior:
            return prior["id"]
        task_id = uuid.uuid4().hex
        c.execute("INSERT INTO queue_jobs(id,interview_id,source_sha256,options,state,created_at) VALUES(?,?,?,?,'queued',?)",
                  (task_id, key, item["sha256"], encoded, core.now()))
        core.event(c, key, "queued", {"queue_id": task_id, "options": options})
        return task_id


def cancel(task_id):
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM queue_jobs WHERE id=? AND state='queued'", (task_id,)).fetchone()
        if row:
            c.execute("UPDATE queue_jobs SET state='cancelled',finished_at=? WHERE id=? AND state='queued'", (core.now(), task_id))
            core.event(c, row["interview_id"], "queue_cancelled", {"queue_id": task_id})


def process_one(runner=None):
    if core.worker_active():
        return False
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM queue_jobs WHERE state='queued' ORDER BY seq LIMIT 1").fetchone()
        if row is None:
            return False
        row = dict(row)
        c.execute("UPDATE queue_jobs SET state='running' WHERE id=?", (row["id"],))
    try:
        with core.db() as c:
            item = c.execute("SELECT sha256 FROM interviews WHERE id=?", (row["interview_id"],)).fetchone()
        if item is None or item["sha256"] != row["source_sha256"]:
            raise ValueError("排队期间录屏已变化，请检查后重新提交")
        result = (runner or core.transcribe)(row["interview_id"], **json.loads(row["options"]))
    except core.WorkerBusy:
        with db() as c:
            c.execute("UPDATE queue_jobs SET state='queued' WHERE id=?", (row["id"],))
        return False
    except Exception as exc:
        with db() as c:
            c.execute("UPDATE queue_jobs SET state='failed',finished_at=?,error=? WHERE id=?", (core.now(), str(exc), row["id"]))
            core.event(c, row["interview_id"], "queue_failed", {"queue_id": row["id"], "error": str(exc)})
    else:
        with db() as c:
            c.execute("UPDATE queue_jobs SET state='completed',finished_at=?,run_id=? WHERE id=?", (core.now(), result["id"], row["id"]))
    return True


def dispatcher_active():
    paths.ensure_data_directory(core.DATA)
    with (core.DATA / "queue.lock").open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


def ensure_dispatcher():
    if not dispatcher_active():
        with (core.DATA / "queue.log").open("ab") as log:
            subprocess.Popen([sys.executable, str(core.ROOT / "queue_jobs.py")], cwd=core.ROOT,
                             stdout=log, stderr=log, start_new_session=paths.child_starts_session(), env=paths.child_environment(core.DATA))


def serve():
    paths.ensure_data_directory(core.DATA)
    with (core.DATA / "queue.lock").open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        with db() as c:
            c.execute("UPDATE queue_jobs SET state='interrupted',finished_at=?,error=? WHERE state='running'",
                      (core.now(), "队列进程中断，请核对已有版本后重新提交"))
        while True:
            process_one()
            time.sleep(2)



def revise(task_id, options):
    """Edit a waiting task atomically; a dispatcher race must never change a running job."""
    if options.get('model') not in core.MODELS or options.get('language') not in core.LANGUAGES:
        raise ValueError('请选择支持的模型和语言')
    if options.get('start', 0) < 0 or options.get('limit', 0) < 0 or not 10 <= options.get('chunk_seconds', 25) <= 60:
        raise ValueError('转写范围或分段时长不正确')
    encoded = json.dumps(options, sort_keys=True, ensure_ascii=False)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute("SELECT * FROM queue_jobs WHERE id=? AND state='queued'", (task_id,)).fetchone()
        if row is None:
            raise ValueError('任务已开始或已结束，不能修改；请新建一次转写。')
        duplicate = c.execute("SELECT id FROM queue_jobs WHERE id!=? AND interview_id=? AND source_sha256=? AND options=? AND state IN ('queued','running')",
                              (task_id, row['interview_id'], row['source_sha256'], encoded)).fetchone()
        if duplicate:
            raise ValueError('已有相同参数的活动任务，请取消重复的等待任务。')
        c.execute('UPDATE queue_jobs SET options=? WHERE id=?', (encoded, task_id))
        core.event(c, row['interview_id'], 'queue_updated', {'queue_id': task_id,
                   'before': json.loads(row['options']), 'after': options})


if __name__ == "__main__":
    serve()
