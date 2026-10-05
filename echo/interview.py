"""Local, append-only interview transcription ledger (Apple Silicon)."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid
import wave
from echo import paths
from echo.asr_models import DEFAULT_MODEL, MODELS, LANGUAGES, transcribe_audio, load_default_model

ROOT = Path(__file__).resolve().parents[1]
DATA = paths.data_directory(ROOT)
SOURCE = ROOT / "recordings"  # Legacy import compatibility only; never an implicit scan target.
MODEL = DEFAULT_MODEL
MEDIA = {".mov", ".mp4", ".mkv", ".wav", ".mp3", ".m4a", ".webm"}
PROMPT = "以下是中文技术面试，可能包含 Python、SQL、MySQL、Redis、Transformer、Attention、LLM、RAG、LoRA、SFT、DPO、PPO、Agent、推荐系统、Hive、Schema Linking、NL2SQL、rerank、召回、精排。"


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    paths.ensure_data_directory(DATA)
    connection = sqlite3.connect(DATA / "ledger.sqlite3", timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS interviews (
            id TEXT PRIMARY KEY, path TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
            sha256 TEXT NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
            discovered_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY, interview_id TEXT NOT NULL REFERENCES interviews(id),
            source_sha256 TEXT NOT NULL, model TEXT NOT NULL, options TEXT NOT NULL,
            state TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
            output_dir TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS events (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
            interview_id TEXT, kind TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS progress (
            run_id TEXT PRIMARY KEY REFERENCES runs(id), stage TEXT NOT NULL,
            done INTEGER NOT NULL, total INTEGER NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notes (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, interview_id TEXT NOT NULL REFERENCES interviews(id),
            at TEXT NOT NULL, body TEXT NOT NULL);
    """)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def event(c, key, kind, detail):
    c.execute("INSERT INTO events(at,interview_id,kind,detail) VALUES(?,?,?,?)",
              (now(), key, kind, json.dumps(detail, ensure_ascii=False)))


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def scan(source=None, settle_seconds=60):
    source = Path(source).expanduser().resolve() if source is not None else paths.source_directory(DATA)
    if not source.is_dir():
        raise ValueError(f"录屏目录不存在：{source}")
    counts = dict(added=0, changed=0, unchanged=0, deferred=0)
    excluded = paths.source_exclusions(DATA, source)
    candidates = []
    for directory, folders, files in os.walk(source):
        folders[:] = sorted(name for name in folders if not (Path(directory) / name).is_symlink()
                            and not any((Path(directory) / name).resolve().is_relative_to(root) for root in excluded))
        candidates.extend(Path(directory) / name for name in sorted(files))
    for path in candidates:
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in MEDIA:
            continue
        if any(path.resolve().is_relative_to(root) for root in excluded):
            continue
        stat = path.stat()
        if time.time() - stat.st_mtime < settle_seconds or not stat.st_size:
            counts["deferred"] += 1
            continue
        with db() as c:
            old = c.execute("SELECT * FROM interviews WHERE path=?", (str(path),)).fetchone()
        if old and (old["size"], old["mtime_ns"]) == (stat.st_size, stat.st_mtime_ns):
            counts["unchanged"] += 1
            continue
        sha = digest(path)
        after = path.stat()
        if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
            counts["deferred"] += 1
            continue
        with db() as c:
            # Re-read under a write transaction: concurrent scans must not duplicate discovery.
            c.execute("BEGIN IMMEDIATE")
            old = c.execute("SELECT * FROM interviews WHERE path=?", (str(path),)).fetchone()
            if old:
                changed = sha != old["sha256"]
                c.execute("UPDATE interviews SET sha256=?,size=?,mtime_ns=?,updated_at=? WHERE id=?",
                          (sha, stat.st_size, stat.st_mtime_ns, now(), old["id"]))
                if changed:
                    event(c, old["id"], "source_changed", {"before": old["sha256"], "after": sha})
                counts["changed" if changed else "unchanged"] += 1
            else:
                key = uuid.uuid4().hex[:12]
                c.execute("INSERT INTO interviews VALUES(?,?,?,?,?,?,?,?)",
                          (key, str(path), path.parent.name + " / " + path.stem,
                           sha, stat.st_size, stat.st_mtime_ns, now(), now()))
                event(c, key, "discovered", {"path": str(path), "sha256": sha})
                counts["added"] += 1
    return counts


def interviews(source=None):
    """Read the preserved ledger, optionally restricted to one selected folder."""
    with db() as c:
        items = [dict(r) for r in c.execute("SELECT * FROM interviews ORDER BY path")]
    if source is not None:
        items = [item for item in items if paths.media_in_source(DATA, item["path"], source=source)]
    return items


def runs(key):
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM runs WHERE interview_id=? ORDER BY started_at DESC", (key,))]


def stamp(seconds, srt=False):
    ms = max(0, round(float(seconds) * 1000))
    seconds, ms = divmod(ms, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}{',' if srt else '.'}{ms:03}"


def review_reasons(segment):
    reasons = []
    if segment.get("audio_rms_db", 0) < -45:
        reasons.append("音量极低，可能为静音幻觉")
    if segment.get("repeated_text"):
        reasons.append("连续出现相同文本")
    if segment.get("avg_logprob", 0) < -0.8:
        reasons.append("识别置信度偏低")
    if segment.get("no_speech_prob", 0) > 0.6:
        reasons.append("可能为静音或背景声")
    if segment.get("compression_ratio", 0) > 2.4:
        reasons.append("可能重复识别")
    return reasons


def annotate_audio(result, audio):
    """Keep raw segments for audit; clamp timestamps and flag weak audio evidence."""
    import numpy as np
    with wave.open(str(audio), "rb") as w:
        rate = w.getframerate()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    duration = len(samples) / rate
    result = dict(result)
    result["raw_segments"] = result.get("raw_segments", result.get("segments", []))
    cleaned = []
    for segment in result["raw_segments"]:
        s = dict(segment)
        s["start"] = max(0, min(duration, float(s["start"])))
        s["end"] = max(s["start"], min(duration, float(s["end"])))
        if s["end"] <= s["start"]:
            continue
        clip = samples[int(s["start"] * rate):int(s["end"] * rate)].astype(np.float64)
        rms = float(np.sqrt(np.mean(clip * clip))) / 32768 if len(clip) else 0
        s["audio_rms_db"] = round(float(20 * np.log10(max(rms, 1e-10))), 1)
        s["repeated_text"] = bool(cleaned and cleaned[-1]["text"].strip() == s["text"].strip())
        cleaned.append(s)
    result["segments"] = cleaned
    result["text"] = "".join(s["text"] for s in cleaned)
    result["duration"] = duration
    return result


def export_result(result, output, metadata):
    names = ('transcript.json', 'transcript.txt', 'transcript.srt', 'review.md', 'review.json', 'audio.mp3')
    if any((output / name).exists() for name in names):
        raise FileExistsError("结果目录已有文件，拒绝覆盖历史结果")
    output.mkdir(parents=True, exist_ok=True)
    segments = result.get("segments", [])
    (output / "transcript.json").write_text(json.dumps({**metadata, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "transcript.txt").write_text(result.get("text", "").strip() + "\n", encoding="utf-8")
    subtitle = []
    markdown = [f"# {metadata['title']}", "", f"模型：{metadata['model']}",
                f"转写范围：{metadata['scope']}", "", "自动转写草稿；未区分说话人，技术术语需结合录音核对。", "", "## 带时间戳记录", ""]
    if result.get("timestamp_kind") == "audio_chunk":
        markdown[6:6] = ["时间戳为音频块范围，适合定位回听；不是逐句或逐字对齐。", ""]
    flagged = []
    for i, s in enumerate(segments, 1):
        text = s["text"].strip()
        subtitle.append(f"{i}\n{stamp(s['start'], True)} --> {stamp(s['end'], True)}\n{text}\n")
        markdown.append(f"- [{stamp(s['start'])}] {text}")
        reasons = review_reasons(s)
        if reasons:
            flagged.append({"start": s["start"], "end": s["end"], "text": text, "reasons": reasons})
    markdown += ["", "## 待核对片段", "", "这些标记是启发式提示，并非准确率测量。", ""]
    markdown += [f"- [{stamp(s['start'])}] {'、'.join(s['reasons'])}：{s['text']}" for s in flagged] or ["未触发自动标记；不代表转写无误。"]
    (output / "transcript.srt").write_text("\n".join(subtitle), encoding="utf-8")
    (output / "review.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    (output / "review.json").write_text(json.dumps(flagged, ensure_ascii=False, indent=2), encoding="utf-8")


class WorkerBusy(RuntimeError):
    pass


@contextmanager
def worker_lock():
    paths.ensure_data_directory(DATA)
    with (DATA / "worker.lock").open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkerBusy("已有转写任务在运行，请稍后重试。") from None
        try:
            # Only the lock owner can recover an interrupted worker.
            with db() as c:
                for row in c.execute("SELECT id,interview_id FROM runs WHERE state='running'").fetchall():
                    c.execute("UPDATE runs SET state='interrupted',finished_at=?,error=? WHERE id=?",
                              (now(), "上次进程意外中断，可重新转写", row["id"]))
                    event(c, row["interview_id"], "interrupted", {"run_id": row["id"]})
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def worker_active():
    if not (DATA / "worker.lock").exists():
        return False
    with (DATA / "worker.lock").open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(f, fcntl.LOCK_UN)
        return False


def transcribe(key, *, model=MODEL, limit=0, prompt=PROMPT, force=False, engine=None, reuse_run=None,
               start=0, language="zh", chunk_seconds=25, silence_filter=True, output_dir=None):
    if limit < 0 or start < 0:
        raise ValueError("起始位置和试听时长不能为负数")
    if model not in MODELS or language not in LANGUAGES:
        raise ValueError("请选择支持的模型和语言")
    if not 10 <= chunk_seconds <= 60:
        raise ValueError("分段时长需在 10–60 秒之间")
    with worker_lock():
        with db() as c:
            item = c.execute("SELECT * FROM interviews WHERE id=?", (key,)).fetchone()
            if item is None:
                raise ValueError("未找到面试，请先扫描目录")
            item = dict(item)
            settings = {"language": language, "limit": limit, "start": start, "prompt": prompt,
                        "precision": MODELS[model].precision, "backend": MODELS[model].backend,
                        "chunk_seconds": chunk_seconds if MODELS[model].backend == "qwen" else None,
                        "silence_filter": silence_filter if MODELS[model].backend == "qwen" else None,
                        "review_revision": 3}
            if reuse_run:
                original = c.execute("SELECT * FROM runs WHERE id=? AND interview_id=? AND source_sha256=? AND model=? AND state='completed'", (reuse_run, key, item["sha256"], model)).fetchone()
                if original is None:
                    raise ValueError("只能复用本场面试当前源文件和相同模型的已完成版本")
                original_options = json.loads(original["options"])
                if original_options["limit"] != limit or original_options["prompt"] != prompt or original_options.get("start", 0) != start or original_options.get("language", "zh") != language:
                    raise ValueError("复用版本的转写范围和术语提示必须一致")
                cached_result = json.loads(Path(original["output_dir"], "transcript.json").read_text(encoding="utf-8"))["result"]
                engine = lambda *a, **kw: cached_result
                for setting in ("chunk_seconds", "silence_filter", "precision", "backend"):
                    if setting in original_options:
                        settings[setting] = original_options[setting]
                settings["based_on_run"] = reuse_run
            options = json.dumps(settings, sort_keys=True, ensure_ascii=False)
            previous = c.execute("SELECT * FROM runs WHERE interview_id=? AND source_sha256=? AND model=? AND options=? AND state='completed' ORDER BY started_at DESC LIMIT 1",
                                 (key, item["sha256"], model, options)).fetchone()
        path = Path(item["path"])
        if digest(path) != item["sha256"]:
            raise ValueError("源录屏已变化，请先重新扫描")
        if previous and not force and Path(previous["output_dir"], "transcript.json").is_file():
            return dict(previous)
        run_id = uuid.uuid4().hex
        output_root = Path(output_dir).expanduser().resolve() if output_dir else paths.output_directory(DATA)
        paths.ensure_data_directory(output_root)
        output = paths.mirrored_output(DATA, path, output_root)
        # Never overwrite an earlier result; additional runs live in a version directory.
        if any((output / name).exists() for name in ('transcript.json', 'transcript.txt', 'transcript.srt', 'review.md', 'review.json', 'audio.mp3')):
            output = output / "versions" / run_id
        with db() as c:
            c.execute("INSERT INTO runs(id,interview_id,source_sha256,model,options,state,started_at) VALUES(?,?,?,?,?,'running',?)",
                      (run_id, key, item["sha256"], model, options, now()))
            event(c, key, "started", {"run_id": run_id, "model": model, "options": json.loads(options)})
        audio = DATA / f"audio-{run_id}.wav"
        try:
            def progress(stage, done=0, total=0):
                with db() as c:
                    c.execute("INSERT OR REPLACE INTO progress VALUES(?,?,?,?,?)", (run_id, stage, done, total, now()))
                print(f"{stage} {done}/{total}" if total else stage, flush=True)
            progress("提取音轨")
            cmd = ["ffmpeg", "-v", "error", "-nostdin"]
            if start:
                cmd += ["-ss", str(start)]
            cmd += ["-i", str(path)]
            if limit:
                cmd += ["-t", str(limit)]
            cmd += ["-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            with wave.open(str(audio), "rb") as w:
                if w.getnframes() == 0:
                    raise ValueError("起始位置超出录屏时长，未找到可转写音频")
            if engine is None:
                os.environ.setdefault("HF_HOME", str(DATA / "models"))
                result = transcribe_audio(audio, key=model, data=DATA, language=language,
                    prompt=prompt, chunk_seconds=chunk_seconds, silence_filter=silence_filter, progress=progress)
            else:
                result = engine(str(audio), path_or_hf_repo=model, language=language,
                    initial_prompt=prompt, fp16=MODELS[model].precision == "FP16")
            result = annotate_audio(result, audio)
            result["source_start"] = start
            for segment in result["segments"]:
                segment["start"] += start
                segment["end"] += start
            progress("保存转写与核对稿")
            if digest(path) != item["sha256"]:
                raise ValueError("转写期间源录屏发生变化，结果不入库；请重新扫描")
            paths.ensure_data_directory(DATA)
            paths.ensure_data_directory(output_root)
            export_result(result, output, {"title": item["title"], "model": model,
                          "source_sha256": item["sha256"], "run_id": run_id,
                          "scope": f"{stamp(start)[:8]}–{stamp(start + result['duration'])[:8]}" if start or limit else "完整录屏", "created_at": now(), "options": settings})
            subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(audio),
                            "-c:a", "libmp3lame", "-b:a", "64k", str(output / "audio.mp3")],
                           check=True, capture_output=True, text=True)
            with db() as c:
                c.execute("UPDATE runs SET state='completed',finished_at=?,output_dir=? WHERE id=?", (now(), str(output), run_id))
                event(c, key, "completed", {"run_id": run_id, "output_dir": str(output)})
            progress("已完成", 1, 1)
        except BaseException as exc:
            detail = exc.stderr if isinstance(exc, subprocess.CalledProcessError) else str(exc)
            detail = detail or type(exc).__name__
            with db() as c:
                c.execute("UPDATE runs SET state='failed',finished_at=?,error=? WHERE id=?", (now(), detail, run_id))
                event(c, key, "failed", {"run_id": run_id, "error": detail})
            raise
        finally:
            audio.unlink(missing_ok=True)
        return next(r for r in runs(key) if r["id"] == run_id)


def add_note(key, body):
    if not body.strip():
        raise ValueError("记录内容不能为空")
    with db() as c:
        c.execute("INSERT INTO notes(interview_id,at,body) VALUES(?,?,?)", (key, now(), body.strip()))
        event(c, key, "note_added", {"characters": len(body.strip())})


def main():
    parser = argparse.ArgumentParser(description="面试录屏解析助手：增量建档、本地转写、版本留痕")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("scan", "watch"):
        p = sub.add_parser(name)
        p.add_argument("--source", type=Path, default=None)
        p.add_argument("--settle-seconds", type=int, default=60)
        if name == "watch":
            p.add_argument("--interval", type=int, default=60)
            p.add_argument("--process", action="store_true", help="自动转写新增/变更文件；失败记录需手动重试")
    sub.add_parser("list")
    sub.add_parser("models")
    p = sub.add_parser("paths", help="查看或保存输入、输出目录")
    p.add_argument("--source", type=Path)
    p.add_argument("--output", type=Path)
    p = sub.add_parser("transcribe")
    p.add_argument("id")
    p.add_argument("--output", type=Path, help="本次转写的输出根目录")
    p.add_argument("--model", default=load_default_model(DATA), choices=MODELS)
    p.add_argument("--start", type=int, default=0, help="从录屏第 N 秒开始")
    p.add_argument("--language", choices=LANGUAGES, default="zh")
    p.add_argument("--chunk-seconds", type=int, default=25, help="Qwen 音频块时长，10–60 秒")
    p.add_argument("--no-silence-filter", action="store_true", help="关闭 Qwen 近乎静音过滤")
    p.add_argument("--limit", type=int, default=0, help="只转写前 N 秒；0 为完整录屏")
    p.add_argument("--prompt", default=PROMPT)
    p.add_argument("--force", action="store_true")
    p.add_argument("--reuse-run", help="复用已有模型结果，仅生成新版核对稿，并保留来源版本记录")
    p = sub.add_parser("note")
    p.add_argument("id")
    p.add_argument("body")
    sub.add_parser("history")
    args = parser.parse_args()
    try:
        if args.command == "models":
            for key, spec in MODELS.items():
                print(key, "—", spec.name, "—", spec.description)
        elif args.command == "paths":
            if args.source is not None or args.output is not None:
                config = paths._config(DATA)
                use_default_output = (args.output is None and not os.environ.get("INTERVIEW_OUTPUT_DIR")
                                      and (not config.get("output") or config.get("output_mode") == "default"))
                paths.save_directories(DATA, args.source or paths.source_directory(DATA),
                                       args.output or paths.output_directory(DATA), default_output=use_default_output)
            source = paths.source_directory(DATA, required=False)
            print(json.dumps({"data": str(DATA), "source": str(source) if source is not None else None, "output": str(paths.output_directory(DATA))}, ensure_ascii=False, indent=2))
        elif args.command == "scan":
            print(json.dumps(scan(args.source, args.settle_seconds), ensure_ascii=False))
        elif args.command == "watch":
            if args.interval < 10 or args.settle_seconds < 0:
                parser.error("扫描间隔至少 10 秒，稳定时间不能为负数")
            while True:
                active_source = args.source.expanduser().resolve() if args.source is not None else paths.source_directory(DATA)
                print(now(), scan(active_source, args.settle_seconds), flush=True)
                if args.process:
                    active_model = load_default_model(DATA)
                    for item in interviews(active_source):
                        prior = [r for r in runs(item["id"]) if r["source_sha256"] == item["sha256"] and r["model"] == active_model and json.loads(r["options"])["limit"] == 0 and json.loads(r["options"]).get("start", 0) == 0]
                        if not prior:
                            try:
                                transcribe(item["id"], model=active_model)
                            except Exception as exc:
                                print(f"{item['id']}: {exc}", file=sys.stderr, flush=True)
                time.sleep(args.interval)
        elif args.command == "list":
            for item in interviews():
                print(item["id"], item["title"], runs(item["id"])[0]["state"] if runs(item["id"]) else "待转写")
        elif args.command == "transcribe":
            print(json.dumps(transcribe(args.id, model=args.model, limit=args.limit, prompt=args.prompt, force=args.force, reuse_run=args.reuse_run, start=args.start, language=args.language, chunk_seconds=args.chunk_seconds, silence_filter=not args.no_silence_filter, output_dir=args.output), ensure_ascii=False))
        elif args.command == "note":
            add_note(args.id, args.body)
        elif args.command == "history":
            with db() as c:
                print(json.dumps([dict(r) for r in c.execute("SELECT * FROM events ORDER BY seq")], ensure_ascii=False, indent=2))
    except KeyboardInterrupt:
        print("已停止；已保存记录保留。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
