"""Whisper Turbo dispatch. Workers use a workspace lock and a separate process."""

import argparse
import fcntl
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

from .store import Problem, Store, now, uid


def valid_model(directory):
    if not directory:
        return False
    path = Path(directory)
    return (path / "config.json").is_file() and any(
        (path / name).is_file() for name in ("weights.npz", "weights.safetensors")
    )


def launch(store, model_dir):
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "server.asr",
            "--data-dir",
            str(store.root),
            "--model-dir",
            str(model_dir or ""),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def submit(store, key, model_dir):
    store.recording_row(key)
    if not valid_model(model_dir):
        raise Problem("请用 --model-dir 或 ECHO_WHISPER_MODEL 指定本机 Whisper Turbo 模型目录。", 422)
    with store.db() as db:
        db.execute("BEGIN IMMEDIATE")
        previous = db.execute(
            "SELECT id,state FROM jobs WHERE recording_id=? AND state IN ('queued','running')", (key,)
        ).fetchone()
        if previous:
            job = dict(previous)
        else:
            job = {"id": uid(), "state": "queued"}
            db.execute(
                "INSERT INTO jobs(id,recording_id,state,created_at) VALUES(?,?,?,?)",
                (job["id"], key, "queued", now()),
            )
    launch(store, model_dir)
    return job


def transcribe(store, key, model_dir, job_id):
    if not valid_model(model_dir):
        raise Problem("本地 Whisper Turbo 模型目录不可用。")
    try:
        import mlx_whisper
    except ModuleNotFoundError as exc:
        raise Problem("当前 Python 环境缺少 mlx-whisper；请在 Apple Silicon 上安装后重试。") from exc
    except ImportError as exc:
        raise Problem("Whisper 运行时不可用，请检查 Metal GPU 及当前 Python 环境。") from exc
    row = store.recording_row(key)
    asset = store.assets / row["asset"]
    if not store.asset_matches(asset, row["sha"]):
        raise Problem("待转写媒体已变化。")
    with tempfile.TemporaryDirectory(dir=store.root) as staging:
        audio = Path(staging) / "input.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(asset),
                "-map",
                "0:a:0",
                "-vn",
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(audio),
            ],
            check=True,
            capture_output=True,
        )
        with wave.open(str(audio), "rb") as handle:
            duration = handle.getnframes() / handle.getframerate()
        output = mlx_whisper.transcribe(
            str(audio),
            path_or_hf_repo=str(Path(model_dir).resolve()),
            task="transcribe",
            fp16=True,
            condition_on_previous_text=False,
            verbose=False,
        )
        for segment in output["segments"]:
            # Whisper can round the final model timestamp beyond the observed PCM.
            segment["start"] = min(duration, max(0, float(segment["start"])))
            segment["end"] = min(duration, max(segment["start"], float(segment["end"])))
        output.update(source_start=0, duration=duration, timestamp_kind="model_segment")
    if not store.asset_matches(asset, row["sha"]):
        raise Problem("原媒体在转写期间变化，结果未登记。")
    return store.import_transcript(
        key, {"model": "Whisper Turbo", "source_sha256": row["sha"], "result": output}, job_id=job_id
    )


def drain(store, model_dir):
    # Blocking flock: a worker spawned just as its predecessor exits will still
    # consume work. At most one process may claim an ASR job for this workspace.
    with (store.root / "asr.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with store.db() as db:
            db.execute(
                "UPDATE jobs SET state='failed',error='上次推理进程中断，可重新转写。' WHERE state='running'"
            )
        while True:
            with store.db() as db:
                db.execute("BEGIN IMMEDIATE")
                job = db.execute(
                    "SELECT * FROM jobs WHERE state='queued' ORDER BY created_at LIMIT 1"
                ).fetchone()
                if not job:
                    return
                db.execute("UPDATE jobs SET state='running' WHERE id=?", (job["id"],))
            # Some native MLX errors terminate their process before Python can
            # catch them. Keep the queue supervisor alive to expose failure.
            try:
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "server.asr",
                        "--data-dir",
                        str(store.root),
                        "--model-dir",
                        str(model_dir),
                        "--job",
                        job["id"],
                    ],
                    check=False,
                )
                reason = f"模型进程异常退出（{proc.returncode}），请检查可用内存、Metal 和模型文件。"
            except OSError as exc:
                reason = str(exc)
            with store.db() as db:
                db.execute(
                    "UPDATE jobs SET state='failed',error=? WHERE id=? AND state='running'",
                    (reason, job["id"]),
                )


def perform(store, model_dir, job_id):
    with store.db() as db:
        job = db.execute(
            "SELECT recording_id FROM jobs WHERE state='running' AND id=?", (job_id,)
        ).fetchone()
    if job:
        try:
            transcribe(store, job["recording_id"], model_dir, job_id)
        except Exception as exc:
            with store.db() as db:
                db.execute(
                    "UPDATE jobs SET state='failed',error=? WHERE id=? AND state='running'",
                    (str(exc)[:2000], job_id),
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--job")
    args = parser.parse_args()
    if args.job:
        perform(Store(args.data_dir), args.model_dir, args.job)
    else:
        drain(Store(args.data_dir), args.model_dir)
