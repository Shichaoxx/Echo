"""Same-origin local HTTP adapter for the Echo Web workbench.

No model loads, directory scans, or dispatchers run on import. A process serves one
explicit data directory, preserving the CLI's single-workspace worker contract.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
import errno
import hmac
import importlib.util
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
import os
import platform
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

from echo import interview as core, library, memory, paths, queue_jobs as queue, workspace_review, storage
from echo.asr_models import LANGUAGES, MODELS, load_default_model, model_ready, save_default_model
from echo.transcription_ui import media_duration, range_error

ROOT = Path(__file__).resolve().parents[1]
COOKIE = "echo_session"
MAX_BODY = 2_000_000
ASSETS = {".css", ".js", ".svg", ".png", ".ico", ".woff", ".woff2"}


class APIError(Exception):
    def __init__(self, status, message, **detail):
        self.status, self.message, self.detail = status, message, detail
        super().__init__(message)


def _number(value, name, *, minimum=0, maximum=10_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise APIError(400, f"{name}必须是有限数字")
    if not minimum <= value <= maximum:
        raise APIError(400, f"{name}超出允许范围")
    return value


def _text(value, name, maximum=100_000):
    if not isinstance(value, str) or len(value) > maximum:
        raise APIError(400, f"{name}格式或长度不正确")
    return value


class WebService:
    def __init__(self, data_dir, ui_dir=None):
        self.data = Path(data_dir).expanduser().absolute()
        paths.ensure_data_directory(self.data)
        stat = self.data.stat()
        self.data_identity = (stat.st_dev, stat.st_ino)
        core.DATA = self.data
        self.ui = Path(ui_dir or ROOT / "web").resolve()
        self.version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.lock = threading.RLock()
        self.sessions = {}
        self.waveforms = OrderedDict()
        self.waveform_lock = threading.Lock()
        # Keep the original table name so existing notes need no migration.
        with core.db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS desktop_drafts (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                interview_id TEXT NOT NULL REFERENCES interviews(id),
                revision INTEGER NOT NULL, at TEXT NOT NULL, body TEXT NOT NULL,
                citations TEXT NOT NULL, UNIQUE(interview_id,revision))""")

    def available(self):
        """Observe the opened root without ever creating or replacing it."""
        try:
            stat = self.data.stat()
            return self.data.is_dir() and (stat.st_dev, stat.st_ino) == self.data_identity
        except OSError:
            return False

    def require_available(self):
        if not self.available():
            raise APIError(503, "数据目录已移动、删除或替换。请重新选择现有目录并重启 Echo。")

    def settings(self):
        source, output = paths.source_directory(self.data, required=False), paths.output_directory(self.data)
        return {"data": str(self.data), "source": str(source) if source else "", "output": str(output),
                "output_layout": paths._config(self.data).get("output_layout", "mirror"),
                "managed": storage.root(self.data) is not None, "source_configured": source is not None,
                "source_exists": bool(source and source.is_dir()), "output_exists": output.is_dir(),
                "output_mode": "custom" if os.environ.get("INTERVIEW_OUTPUT_DIR") else paths._config(self.data).get("output_mode", "custom" if paths._config(self.data).get("output") else "default")}

    def directories(self, value):
        raw = _text(value.get("path", ""), "目录路径", 8192).strip()
        target = Path(raw).expanduser() if raw else Path.home()
        if not target.is_absolute():
            raise APIError(400, "请输入完整目录路径。")
        try:
            requested = target
            if value.get("initial") is True:
                while not target.exists() and target != target.parent:
                    target = target.parent
            fallback = target != requested
            target = target.resolve(strict=True)
            if not target.is_dir():
                raise APIError(400, "请选择文件夹，而不是文件。")
            folders = []
            with os.scandir(target) as entries:
                for entry in entries:
                    if entry.name.startswith(".") and not value.get("hidden", False):
                        continue
                    try:
                        if entry.is_dir():
                            folders.append({"name": entry.name, "path": str(target / entry.name)})
                    except OSError:
                        continue
                    if len(folders) > 2000:
                        raise APIError(400, "此目录包含过多文件夹，请输入更具体的路径。")
            folders.sort(key=lambda entry: (entry["name"].casefold(), entry["name"]))
            return {"path": str(target), "parent": str(target.parent) if target.parent != target else None,
                    "directories": folders, "notice": "原路径尚不存在，已打开最近的上级文件夹。" if fallback else ""}
        except FileNotFoundError:
            raise APIError(404, "目录不存在或链接已失效。可返回上一级，或输入已有目录。")
        except PermissionError:
            raise APIError(422, "没有权限浏览此文件夹，请选择其他目录或检查系统权限。")
        except (OSError, RuntimeError):
            raise APIError(400, "无法打开此目录，请检查路径、磁盘连接或文件夹权限。")

    def bootstrap(self):
        self.require_available()
        return {"version": self.version, "settings": self.settings(),
                "models": [{"id": key, "name": spec.name, "description": spec.description,
                            "timing": spec.timing, "backend": spec.backend,
                            "ready": model_ready(key, self.data)} for key, spec in MODELS.items()],
                "languages": LANGUAGES, "default_model": load_default_model(self.data),
                "default_prompt": core.PROMPT, "runtime": self.runtime()}

    @staticmethod
    def runtime():
        def installed(name):
            try:
                return importlib.util.find_spec(name) is not None
            except (ImportError, ValueError):
                return False
        shared = all(installed(name) for name in ("numpy", "mlx", "huggingface_hub"))
        return {"python": sys.executable, "python_version": platform.python_version(),
                "platform": platform.platform(), "ffmpeg": shutil.which("ffmpeg") is not None,
                "ffprobe": shutil.which("ffprobe") is not None,
                "backends": {"qwen": shared and installed("mlx_audio"),
                             "whisper": shared and installed("mlx_whisper")}}

    def item(self, key):
        with core.db() as c:
            row = c.execute("SELECT * FROM interviews WHERE id=?", (key,)).fetchone()
        if not row:
            raise APIError(404, "未找到这份录音")
        return dict(row)

    @staticmethod
    def duration(path):
        try:
            stat = Path(path).stat()
            return media_duration(str(path), stat.st_size, stat.st_mtime_ns)
        except OSError:
            return None

    @staticmethod
    def runs(key):
        rows = core.runs(key)
        for run in rows:
            run["options"] = json.loads(run["options"])
            run["result_available"] = bool(run["output_dir"] and
                                            Path(run["output_dir"], "transcript.json").is_file())
        return rows

    def library(self):
        store = library.read_store(self.data, "library.json")
        jobs = queue.jobs()
        items = []
        with core.db() as c:
            records = [dict(row) for row in c.execute("SELECT * FROM interviews ORDER BY updated_at DESC,path")]
            runs = [dict(row) for row in c.execute("SELECT * FROM runs ORDER BY started_at DESC")]
        source = paths.source_directory(self.data, required=False)
        # Keep the ledger and old runs intact when the user changes folders.
        # Unconfigured legacy workspaces may still contain explicitly imported files.
        if source is not None:
            records = [item for item in records if paths.media_in_source(self.data, item["path"], source=source)]
        latest = {}
        for run in runs:
            latest.setdefault(run["interview_id"], run)
        active = {job["interview_id"]: job["state"] for job in jobs if job["state"] in ("queued", "running")}
        for item in records:
            run = latest.get(item["id"])
            if run:
                run["options"] = json.loads(run["options"]) if isinstance(run["options"], str) else run["options"]
            items.append({**item, "metadata": library.metadata(item, store), "latest_run": run,
                          "state": active.get(item["id"], run["state"] if run else "ready"),
                          "duration": None, "source_exists": Path(item["path"]).is_file()})
        # Duration probing is deferred to the selected record; opening a large
        # library must not spawn one ffprobe process per recording.
        return {"items": items, "count": len(items), "projects": store.get("projects", {})}

    def draft(self, key, connection=None):
        if connection is None:
            with core.db() as c:
                return self.draft(key, c)
        row = connection.execute("SELECT revision,at,body,citations FROM desktop_drafts WHERE interview_id=? ORDER BY revision DESC LIMIT 1", (key,)).fetchone()
        return {**dict(row), "citations": json.loads(row["citations"])} if row else {"body": "", "citations": [], "revision": 0, "at": None}

    @staticmethod
    def result(run):
        if not run or not run.get("result_available"):
            return {}
        file = Path(run["output_dir"], "transcript.json")
        if file.stat().st_size > 100_000_000:
            raise APIError(422, "转写结果过大，请检查结果文件")
        value = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise APIError(422, "转写结果格式不正确")
        result = value.get("result", {})
        if not isinstance(result, dict) or not isinstance(result.get("segments", []), list):
            raise APIError(422, "转写结果格式不正确")
        try:
            for segment in result.get("segments", []):
                if not isinstance(segment, dict):
                    raise APIError(422, "转写片段格式不正确")
                start = _number(segment.get("start"), "片段开始时间")
                _number(segment.get("end"), "片段结束时间", minimum=start)
                _text(segment.get("text"), "转写片段", 1_000_000)
            for field in ("source_start", "duration"):
                if field in result:
                    _number(result[field], field)
        except APIError as exc:
            raise APIError(422, "转写结果包含无效时间或文字，请检查结果文件。") from exc
        return result

    def media(self, item, run=None, result=None, *, cached=False):
        source = Path(item["path"])
        cached_path = Path(run["output_dir"], "audio.mp3") if run and run.get("output_dir") else None
        if cached_path and cached_path.is_file() and (cached or source.suffix.lower() in {".mkv", ".webm"}):
            return cached_path, (result or self.result(run)).get("source_start", 0)
        if not cached and source.is_file() and source.suffix.lower() in core.MEDIA:
            return source, 0
        if cached_path and cached_path.is_file():
            return cached_path, (result or self.result(run)).get("source_start", 0)
        return None, 0

    def waveform(self, media):
        """Read an amplitude envelope lazily, without loading the full recording in RAM."""
        self.require_available()
        stat = media.stat()
        key = (str(media.resolve()), stat.st_size, stat.st_mtime_ns)
        with self.lock:
            if key in self.waveforms:
                self.waveforms.move_to_end(key)
                return self.waveforms[key]
        if not self.waveform_lock.acquire(blocking=False):
            raise APIError(503, "另一份录音的波形正在读取，稍后可重试；媒体仍可正常播放。")
        try:
            ffmpeg = shutil.which("ffmpeg")
            if not ffmpeg:
                raise APIError(422, "未找到 FFmpeg，暂时无法读取波形；仍可使用时间轴播放。")
            import numpy as np
            with tempfile.TemporaryFile(dir=self.data) as pcm:
                try:
                    result = subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(media),
                                             "-map", "0:a:0", "-vn", "-ac", "1", "-ar", "8000",
                                             "-f", "s16le", "pipe:1"], stdout=pcm, stderr=subprocess.PIPE,
                                            timeout=75, check=False)
                except subprocess.TimeoutExpired:
                    raise APIError(422, "波形读取超时，仍可直接播放或稍后重试。")
                if result.returncode or pcm.tell() == 0:
                    raise APIError(422, "暂时无法读取音轨波形，仍可使用时间轴播放。")
                samples = pcm.tell() // 2
                block = max(1, math.ceil(samples / 512))
                pcm.seek(0)
                peaks = []
                while chunk := pcm.read(block * 2):
                    peaks.append(round(float(np.max(np.abs(np.frombuffer(chunk, dtype='<i2').astype(np.int32)))) / 32768, 5))
            self.require_available()
            latest = media.stat()
            if (latest.st_size, latest.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                raise APIError(409, "录音已更新，请重新打开后读取波形。")
            envelope = {"peaks": peaks, "duration": samples / 8000}
            with self.lock:
                self.waveforms[key] = envelope
                while len(self.waveforms) > 16:
                    self.waveforms.popitem(last=False)
            return envelope
        finally:
            self.waveform_lock.release()

    def details(self, key, run_id=None):
        item, runs = self.item(key), self.runs(key)
        preferred = storage.selected_run(self.data, key)
        run = next((r for r in runs if r["id"] == run_id), None) if run_id else (
            next((r for r in runs if r['id'] == preferred and r['state'] == 'completed' and r['result_available']), None)
            or next((r for r in runs if r["state"] == "completed" and r['result_available']), None))
        if run_id and run is None:
            raise APIError(404, "这份录音没有指定的转写版本")
        result = self.result(run)
        warnings = []
        if preferred and not run_id and (not run or run['id'] != preferred):
            warnings.append('采用版本不可读，暂时展示可用历史版本；请恢复文件或重新选择采用版本。')
        if run and not result:
            warnings.append("这个版本的结果文件已移动或缺失，可新建转写版本。")
        if run and run["source_sha256"] != item["sha256"]:
            warnings.append("当前源文件已更新，所选转写来自旧版本。")
        media, offset = self.media(item, run, result)
        cached, cached_offset = self.media(item, run, result, cached=True)
        duration = self.duration(item["path"]) if Path(item["path"]).is_file() else None
        source_duration = duration
        if duration is None and result:
            duration = result.get("source_start", 0) + result.get("duration", 0)
        review_path = storage.review(self.data, key) or workspace_review.review_path(item["path"], self.data, previous_outputs=[
            row["output_dir"] for row in runs if row["state"] == "completed" and row.get("output_dir")])
        review = workspace_review.read_document(review_path, kind="interview", interview_id=key)
        if review and run and review['metadata'].get('source_run_ids') and run['id'] not in review['metadata']['source_run_ids']:
            warnings.append('当前浏览的转写版本未被这份复盘引用，请核对版本后再使用结论。')
        with core.db() as c:
            notes = [dict(row) for row in c.execute("SELECT seq,at,body FROM notes WHERE interview_id=? ORDER BY seq DESC", (key,))]
        item["metadata"] = library.metadata(item, library.read_store(self.data, "library.json"))
        return {"item": item, "runs": runs, "run": run, "preferred_run_id": preferred,
                "documents": storage.documents(self.data, key), "segments": result.get("segments", []),
                "notes": notes, "draft": self.draft(key),
                "review": {"body": "", **(review or {}), "path": str(review_path), "exists": review_path.is_file()},
                "duration": duration, "source_duration": source_duration, "result_duration": result.get("duration"),
                "source_start": result.get("source_start", 0), "media_offset": offset,
                "media_url": f"/api/interviews/{key}/media" + (f"?run={run['id']}" if run else "") if media else None,
                "cached_media_url": f"/api/interviews/{key}/media?run={run['id']}&cached=1" if cached and run else None,
                "cached_media_offset": cached_offset,
                "source_exists": Path(item["path"]).is_file(), "result_available": bool(result),
                "warnings": warnings, "timestamp_kind": result.get("timestamp_kind")}

    def handoff(self, key, run_id=None):
        from echo.codex_handoff import build_handoff
        return build_handoff(self.details(key, run_id), self.settings(), ROOT)

    def save_draft(self, key, value):
        self.item(key)
        body = _text(value.get("body"), "笔记", 500_000)
        revision = value.get("revision")
        if type(revision) is not int or revision < 0:
            raise APIError(400, "笔记版本不正确")
        citations = value.get("citations", [])
        if not isinstance(citations, list) or len(citations) > 1000:
            raise APIError(400, "引用格式或数量不正确")
        validated, results = [], {}
        for citation in citations:
            if not isinstance(citation, dict):
                raise APIError(400, "引用格式不正确")
            run_id, index = citation.get("run_id"), citation.get("index")
            if not isinstance(run_id, str) or type(index) is not int or index < 0:
                raise APIError(400, "引用的版本和片段不正确")
            if run_id not in results:
                run = next((r for r in self.runs(key) if r["id"] == run_id and r["state"] == "completed"), None)
                if not run:
                    raise APIError(400, "只能引用这份录音的已完成版本")
                results[run_id] = self.result(run).get("segments", [])
            if index >= len(results[run_id]):
                # Existing citations remain editable when result files disappear.
                previous = next((x for x in self.draft(key)["citations"] if x["run_id"] == run_id and x["index"] == index), None)
                if previous is None:
                    raise APIError(400, "引用片段已不可用，请刷新后重试")
                validated.append(previous)
            else:
                segment = results[run_id][index]
                validated.append({"run_id": run_id, "index": index, "start": segment["start"],
                                  "end": segment["end"], "text": segment["text"]})
        # BEGIN IMMEDIATE makes the optimistic check and append atomic across
        # requests/processes; no update/delete of prior drafts or v1 notes.
        with core.db() as c:
            c.execute("BEGIN IMMEDIATE")
            current = self.draft(key, c)
            if current["revision"] != revision:
                raise APIError(409, "笔记已在另一窗口更新，请保留当前文字并载入最新版本。", draft=current)
            if body == current["body"] and validated == current["citations"]:
                return {"draft": current}
            c.execute("INSERT INTO desktop_drafts(interview_id,revision,at,body,citations) VALUES(?,?,?,?,?)",
                      (key, revision + 1, core.now(), body, json.dumps(validated, ensure_ascii=False)))
            return {"draft": self.draft(key, c)}

    def import_media(self, value):
        requested = value.get("paths")
        if not isinstance(requested, list) or not 1 <= len(requested) <= 100:
            raise APIError(400, "请选择 1–100 个录音或录屏文件")
        selected = []
        for raw in requested:
            path = Path(_text(raw, "文件路径", 8192)).expanduser()
            if not path.is_absolute() or not path.is_file() or path.suffix.lower() not in core.MEDIA:
                raise APIError(400, "请选择存在的录音或录屏文件（MOV、MP4、MKV、WAV、MP3、M4A、WebM）")
            path = path.resolve()
            source = paths.source_directory(self.data, required=False)
            if source is not None and not paths.media_in_source(self.data, path, source=source):
                raise APIError(400, "请选择当前面试文件夹内的原始媒体；其他文件夹请先切换目录。")
            stat = path.stat()
            if not stat.st_size:
                raise APIError(400, "不能导入空文件")
            digest = core.digest(path)
            after = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise APIError(409, "文件仍在写入，请录制完成后再导入")
            selected.append((path, stat, digest))
        keys = []
        with core.db() as c:
            c.execute("BEGIN IMMEDIATE")
            for path, stat, digest in selected:
                old = c.execute("SELECT * FROM interviews WHERE path=?", (str(path),)).fetchone()
                if old:
                    key = old["id"]
                    if old["sha256"] != digest:
                        core.event(c, key, "source_changed", {"before": old["sha256"], "after": digest})
                    c.execute("UPDATE interviews SET sha256=?,size=?,mtime_ns=?,updated_at=? WHERE id=?",
                              (digest, stat.st_size, stat.st_mtime_ns, core.now(), key))
                else:
                    key = uuid.uuid4().hex[:12]
                    c.execute("INSERT INTO interviews VALUES(?,?,?,?,?,?,?,?)",
                              (key, str(path), path.stem, digest, stat.st_size, stat.st_mtime_ns, core.now(), core.now()))
                    core.event(c, key, "discovered", {"path": str(path), "sha256": digest, "method": "web_import"})
                keys.append(key)
        items = [item for item in self.library()["items"] if item["id"] in keys]
        return {"items": items, "selected_id": keys[0]}

    def queue_snapshot(self):
        titles = {item["id"]: item["title"] for item in core.interviews()}
        with core.db() as c:
            progress = {row["run_id"]: dict(row) for row in c.execute("SELECT * FROM progress")}
            running = {row["interview_id"]: row["id"] for row in c.execute("SELECT id,interview_id FROM runs WHERE state='running'")}
        jobs = [{**job, "title": titles.get(job["interview_id"], "录音已不可用"),
                 "options": json.loads(job["options"]),
                 "progress": progress.get(job["run_id"] or (running.get(job["interview_id"]) if job["state"] == "running" else None))}
                for job in queue.jobs()]
        return {"jobs": jobs, "worker_active": core.worker_active()}

    def submit(self, value):
        item = self.item(value.get("interview_id"))
        if not Path(item["path"]).is_file():
            raise APIError(400, "源文件已移动或缺失，请重新导入")
        options = value.get("options", {})
        if not isinstance(options, dict):
            raise APIError(400, "转写参数格式不正确")
        defaults = dict(model=load_default_model(self.data), language="zh", start=0, limit=0,
                        prompt=core.PROMPT, force=False, chunk_seconds=25, silence_filter=True)
        if set(options) - set(defaults):
            raise APIError(400, "包含未知转写参数")
        options = {**defaults, **options}
        # Older browser tabs send null for the optional duration. The worker
        # contract uses zero to mean "from the chosen start to the end".
        if options["limit"] is None:
            options["limit"] = 0
        if options["model"] not in MODELS or options["language"] not in LANGUAGES:
            raise APIError(400, "请选择支持的模型和语言")
        for field in ("start", "limit"):
            _number(options[field], {"start": "起点", "limit": "时长"}[field])
        _number(options["chunk_seconds"], "分段时长", minimum=10, maximum=60)
        _text(options["prompt"], "术语提示", 20_000)
        if any(type(options[field]) is not bool for field in ("force", "silence_filter")):
            raise APIError(400, "转写开关必须为布尔值")
        error = range_error(options["start"], options["limit"], self.duration(item["path"]))
        if error:
            raise APIError(400, error)
        runtime = self.runtime()
        if not runtime["ffmpeg"] or not runtime["ffprobe"]:
            raise APIError(400, "当前环境缺少 FFmpeg/ffprobe，请安装后重新打开 Echo。")
        if not runtime["backends"][MODELS[options["model"]].backend]:
            raise APIError(400, "当前 Python 环境缺少所选模型的转写依赖，请安装 requirements-asr.txt 后重启 Echo。")
        task = queue.submit(item["id"], options)
        queue.ensure_dispatcher()
        return {"id": task, "job": next(job for job in self.queue_snapshot()["jobs"] if job["id"] == task)}


class WebHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service, port=0):
        self.service = service
        super().__init__(("127.0.0.1", port), WebHandler)
        self.hosts = {f"localhost:{self.server_port}", f"127.0.0.1:{self.server_port}"}
        self.cookie_name = f"{COOKIE}_{self.server_port}"


class WebHandler(BaseHTTPRequestHandler):
    server_version = "EchoLocal"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, *_args):
        # Filenames, paths and source text never enter web access logs.
        pass

    @property
    def service(self):
        return self.server.service

    def headers_for(self, status, mime, length, **headers):
        self.send_response(status)
        defaults = {"Content-Type": mime, "Content-Length": str(length), "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                    "Cross-Origin-Resource-Policy": "same-origin", "X-Frame-Options": "DENY",
                    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"}
        defaults.update(headers)
        for key, value in defaults.items():
            self.send_header(key.replace("_", "-"), value)
        self.end_headers()

    def json(self, value, status=200, **headers):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        try:
            self.headers_for(status, "application/json; charset=utf-8", len(body), **headers)
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            # Also covers error responses after the caller closes its tab.
            return

    def context(self):
        if len(self.headers.get_all("Host", [])) != 1 or self.headers.get("Host") not in self.server.hosts:
            raise APIError(403, "此服务仅允许本机 Echo 窗口访问")
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + self.headers["Host"]:
            raise APIError(403, "不接受来自其他站点的请求")
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            raise APIError(403, "不接受来自其他站点的请求")
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        token = cookie.get(self.server.cookie_name)
        return self.service.sessions.get(token.value) if token else None

    def handle_request(self):
        try:
            csrf = self.context()
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or "\x00" in self.path:
                raise APIError(400, "请求地址不正确")
            path = unquote(parsed.path)
            query = parse_qs(parsed.query)
            if self.command in ("GET", "HEAD") and path == "/api/health":
                available = self.service.available()
                return self.json({"status": "ok" if available else "unavailable", "version": self.service.version},
                                 200 if available else 503)
            if self.command in ("GET", "HEAD") and path == "/api/bootstrap":
                headers = {}
                if csrf is None:
                    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                    with self.service.lock:
                        if len(self.service.sessions) >= 128:
                            self.service.sessions.pop(next(iter(self.service.sessions)))
                        self.service.sessions[token] = csrf
                    headers["Set-Cookie"] = f"{self.server.cookie_name}={token}; Path=/; HttpOnly; SameSite=Strict"
                return self.json({**self.service.bootstrap(), "csrf": csrf}, **headers)
            if path.startswith("/api/"):
                if not csrf:
                    raise APIError(401, "请重新载入 Echo 窗口")
                self.service.require_available()
                if self.command == "POST":
                    if not hmac.compare_digest(self.headers.get("X-Echo-CSRF", ""), csrf):
                        raise APIError(403, "请求校验已失效，请重新载入窗口")
                    if self.headers.get_content_type() != "application/json":
                        raise APIError(415, "请使用 JSON 请求")
                    raw_length = self.headers.get("Content-Length", "")
                    if not raw_length.isdigit() or int(raw_length) > MAX_BODY or self.headers.get("Transfer-Encoding"):
                        raise APIError(413, "请求长度不正确或过大")
                    value = json.loads(self.rfile.read(int(raw_length)))
                    if not isinstance(value, dict):
                        raise APIError(400, "请求必须是 JSON 对象")
                    with self.service.lock:
                        return self.post_api(path, value)
                return self.get_api(path, query)
            if self.command == "POST":
                raise APIError(404, "未找到此操作")
            asset = self.service.ui / ("index.html" if path == "/" else path.lstrip("/"))
            if path != "/" and asset.suffix not in ASSETS:
                raise APIError(404, "未找到界面资源")
            if not asset.resolve().is_relative_to(self.service.ui) or not asset.is_file():
                raise APIError(404, "未找到界面资源")
            self.file(asset, ranges=False)
        except APIError as exc:
            self.json({"error": exc.message, **exc.detail}, exc.status)
        except memory.MemoryStorageError as exc:
            self.json({"error": str(exc), "code": exc.code}, 409)
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            self.json({"error": "数据格式或参数不正确，请检查后重试。", "detail": str(exc)}, 400)
        except FileNotFoundError:
            self.json({"error": "文件或数据目录已移动，请检查配置后重启 Echo。"}, 404)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            # Navigating away or cancelling a media request is a normal client
            # disconnect. Do not attempt a second response on a closed socket.
            return
        except (OSError, sqlite3.Error):
            self.json({"error": "无法访问本机数据，请检查目录权限和可用空间。"}, 500)

    def get_api(self, path, query):
        service = self.service
        if path == '/api/overview':
            return self.json(storage.overview(service.data, service.library()['items']))
        if path == '/api/material':
            key = query.get('interview', [None])[0]
            if key:
                service.item(key)
            return self.json(storage.read_material(service.data, query.get('id', [''])[0], key))
        if path == "/api/memory":
            return self.json(memory.status(service.data))
        if path == "/api/library":
            return self.json(service.library())
        if path == "/api/queue":
            return self.json(service.queue_snapshot())
        match = re.fullmatch(r"/api/interviews/([a-zA-Z0-9_-]+)(?:/(media|export|waveform|handoff))?", path)
        if not match:
            raise APIError(404, "未找到此操作")
        key, operation = match.groups()
        run_id = query.get("run", [None])[0]
        if not operation:
            return self.json(service.details(key, run_id))
        if operation == "handoff":
            return self.json(service.handoff(key, run_id))
        item, runs = service.item(key), service.runs(key)
        preferred = storage.selected_run(service.data, key)
        run = next((r for r in runs if r["id"] == run_id), None) if run_id else (
            next((r for r in runs if r['id'] == preferred and r['state'] == 'completed' and r['result_available']), None)
            or next((r for r in runs if r["state"] == "completed" and r['result_available']), None))
        if run_id and run is None:
            raise APIError(404, "未找到指定转写版本")
        if operation in ("media", "waveform"):
            media, media_offset = service.media(item, run, cached=query.get("cached", ["0"])[0] == "1")
            if media is None:
                raise APIError(404, "源文件与回听音频均不可用")
            if operation == "waveform":
                return self.json({**service.waveform(media), "offset": media_offset})
            return self.file(media, ranges=True)
        format_ = query.get("format", ["md"])[0]
        if format_ == "md":
            draft = service.draft(key)
            text = f"# {item['title']}\n\n" + draft["body"] + "\n"
            if draft["citations"]:
                text += "\n## 原文引用\n\n" + "\n".join(f"- [{core.stamp(c['start'])[:8]}] {c['text']}（版本 {c['run_id']}）" for c in draft["citations"]) + "\n"
            with core.db() as c:
                notes = c.execute("SELECT at,body FROM notes WHERE interview_id=? ORDER BY seq", (key,)).fetchall()
            if notes:
                text += "\n## 历史追加笔记\n\n" + "\n\n".join(f"### {n['at']}\n\n{n['body']}" for n in notes) + "\n"
            data = text.encode("utf-8")
            self.headers_for(200, "text/markdown; charset=utf-8", len(data), Content_Disposition='attachment; filename="echo-review.md"')
            if self.command != "HEAD":
                self.wfile.write(data)
            return
        filenames = {"txt": "transcript.txt", "srt": "transcript.srt", "json": "transcript.json", "review": "review.md"}
        if format_ not in filenames or not run or not run["output_dir"]:
            raise APIError(404, "此导出格式或转写版本不可用")
        return self.file(Path(run["output_dir"], filenames[format_]), download=filenames[format_])

    def post_api(self, path, value):
        service = self.service
        if path == '/api/navigation':
            key = _text(value.get('interview_id'), '场次', 100)
            item = service.item(key)
            if paths.source_directory(service.data, required=False) and not paths.media_in_source(service.data, item['path']):
                raise APIError(409, '场次不属于当前资料库')
            position = _number(value.get('position', 0), '播放位置')
            run_id = value.get('run_id')
            if run_id is not None and not any(r['id'] == run_id for r in service.runs(key)):
                raise APIError(400, '转写版本不属于当前场次')
            storage.save_navigation(service.data, {'interview_id': key, 'run_id': run_id, 'position': position})
            return self.json({'saved': True})
        if path == "/api/memory/init":
            state = memory.status(service.data)
            if not state["source_directory"]:
                raise APIError(400, "请先选择面试文件夹，再初始化 Memory。")
            if (value.get("source_directory") != state["source_directory"]
                    or value.get("root") != state["root"]):
                raise APIError(409, "资料库或 Memory 位置已变化，请重新打开交接窗口。")
            return self.json(memory.initialize(service.data,
                                              expected_source=value['source_directory'],
                                              expected_root=value['root']))
        if path == "/api/projects":
            return self.json({"projects": library.manage_project(service.data, value)})
        if path == "/api/directories":
            return self.json(service.directories(value))
        if path == "/api/import":
            return self.json(service.import_media(value))
        if path == "/api/scan":
            with service.lock:
                return self.json(core.scan())
        if path == "/api/settings":
            source = _text(value.get("source"), "输入目录", 8192)
            output = _text(value.get("output"), "输出目录", 8192)
            model = value.get("default_model")
            if model is not None and model not in MODELS:
                raise APIError(400, "请选择支持的默认模型")
            # Queued workers resolve output paths when they start. Keep those
            # paths stable until all submitted work finishes or is cancelled.
            with service.lock:
                current = service.settings()
                requested_source = paths._resolve(source, service.data)
                requested_output = requested_source / "echo" if value.get("output_mode") == "default" else paths._resolve(output, service.data)
                changing_paths = (str(requested_source) != current["source"]
                                  or str(requested_output) != current["output"]
                                  or value.get("output_layout", current["output_layout"]) != current["output_layout"])
                if changing_paths and storage.root(service.data) is not None:
                    raise APIError(409, '当前资料库已登记。搬迁时请停止服务并使用资料库重关联工具；切换资料库请使用它的 .state 启动。')
                if changing_paths and (core.worker_active() or any(job["state"] in {"queued", "running"} for job in queue.jobs())):
                    raise APIError(409, "还有转写任务在排队或运行。请等待完成或取消排队后，再切换面试文件夹、输出目录或目录结构。")
                paths.save_directories(service.data, source, output, default_output=value.get("output_mode") == "default", output_layout=value.get("output_layout"))
            if model is not None:
                save_default_model(service.data, model)
            return self.json({"settings": service.settings(), "default_model": load_default_model(service.data)})
        if path == "/api/queue":
            with service.lock:
                return self.json(service.submit(value), 201)
        match = re.fullmatch(r"/api/queue/([a-zA-Z0-9_-]+)/cancel", path)
        if match:
            job = next((job for job in queue.jobs() if job["id"] == match[1]), None)
            if job is None:
                raise APIError(404, "未找到任务")
            if job["state"] != "queued":
                raise APIError(409, "只能取消等待中的任务")
            queue.cancel(match[1])
            job = next(job for job in queue.jobs() if job["id"] == match[1])
            if job["state"] != "cancelled":
                raise APIError(409, "任务已经开始，未中断正在处理的任务")
            return self.json({"id": match[1], "state": "cancelled"})
        match = re.fullmatch(r"/api/interviews/([a-zA-Z0-9_-]+)/(draft|metadata|preferred-run)", path)
        if not match:
            raise APIError(404, "未找到此操作")
        key, operation = match.groups()
        if operation == "draft":
            return self.json(service.save_draft(key, value))
        service.item(key)
        if operation == 'preferred-run':
            run_id = _text(value.get('run_id'), '采用版本', 100)
            run = next((r for r in service.runs(key) if r['id'] == run_id), None)
            if not run or run['state'] != 'completed' or not run['result_available']:
                raise APIError(400, '只能采用本场已完成且文件可读的版本')
            with service.lock:
                storage.select_run(service.data, key, run_id)
            return self.json({'preferred_run_id': run_id})
        fields = {"company", "round", "date", "tags", "archived", "project_id"}
        if set(value) - fields:
            raise APIError(400, "包含未知资料字段")
        for name, field in value.items():
            if name == "archived":
                if type(field) is not bool:
                    raise APIError(400, "归档状态必须为布尔值")
            else:
                _text(field, name, 1000)
        library.update_metadata(service.data, key, **value)
        return self.json({"metadata": library.metadata(service.item(key), library.read_store(service.data, "library.json"))})

    def file(self, path, *, ranges=False, download=None):
        if not path.is_file():
            raise APIError(404, "文件已移动或缺失")
        with path.open("rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            first, last, status = 0, size - 1, 200
            extra = {"Accept-Ranges": "bytes"} if ranges else {}
            requested = self.headers.get("Range") if ranges else None
            if requested:
                match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
                if not match or not any(match.groups()) or not size:
                    return self.unsatisfied(size)
                start, end = match.groups()
                if start:
                    first, last = int(start), min(int(end), size - 1) if end else size - 1
                else:
                    first = max(0, size - int(end))
                if first > last or first >= size:
                    return self.unsatisfied(size)
                status = 206
                extra["Content-Range"] = f"bytes {first}-{last}/{size}"
            if download:
                extra["Content-Disposition"] = f'attachment; filename="{download}"'
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if path.suffix == ".js":
                mime = "text/javascript; charset=utf-8"
            self.headers_for(status, mime, max(0, last - first + 1), **extra)
            if self.command == "HEAD":
                return
            handle.seek(first)
            remaining = last - first + 1
            while remaining > 0:
                block = handle.read(min(256 * 1024, remaining))
                if not block:
                    break
                try:
                    self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(block)

    def unsatisfied(self, size):
        self.headers_for(416, "text/plain", 0, Content_Range=f"bytes */{size}")

    do_GET = handle_request
    do_HEAD = handle_request
    do_POST = handle_request


def main(argv=None):
    parser = argparse.ArgumentParser(description="Echo 本地工作区服务（仅监听 127.0.0.1）")
    parser.add_argument("--data-dir", type=Path, default=paths.data_directory(),
                        help="数据目录；默认 INTERVIEW_DATA_DIR 或仓库的 .local")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--ui-dir", type=Path, default=ROOT / "web")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("端口必须介于 0 与 65535 之间；0 表示自动选择空闲端口。")
    try:
        if not args.ui_dir.is_dir() or not (args.ui_dir / "index.html").is_file():
            raise ValueError("界面资源缺失，请在完整 Echo 仓库中启动，或使用 --ui-dir 指定界面目录。")
        service = WebService(args.data_dir, args.ui_dir)
        # Validate configuration before advertising a successful startup URL.
        service.bootstrap()
        with WebHTTPServer(service, args.port) as server:
            print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/", "pid": os.getpid(), "version": service.version}), flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            message = f"本机端口 {args.port} 已被占用，请使用 --port 0 或选择其他端口。"
        elif isinstance(exc, PermissionError):
            message = "没有权限访问数据目录或绑定本机端口，请检查目录权限与运行环境。"
        elif isinstance(exc, (FileNotFoundError, NotADirectoryError)):
            message = "数据目录、上级目录或运行资源不存在，请检查路径或失效链接。"
        else:
            message = "无法访问本机资源，请检查目录、端口和可用空间。"
        print("Echo 启动失败：" + message, file=sys.stderr)
        return 1
    except (ValueError, sqlite3.Error, APIError) as exc:
        message = str(exc) if isinstance(exc, (ValueError, APIError)) else "无法打开台账，请检查数据库与目录权限。"
        print("Echo 启动失败：" + message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
