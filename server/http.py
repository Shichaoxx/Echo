"""Loopback-only JSON, browser assets, and authenticated range requests."""

import hmac
import json
import mimetypes
import re
import secrets
import sqlite3
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from . import asr
from .store import Problem


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, store, port, ui, model_dir=None):
        super().__init__(("127.0.0.1", port), Handler)
        self.store = store
        self.ui = Path(ui).resolve()
        self.model_dir = model_dir
        self.cookie_name = "echo_v1_" + str(self.server_port)
        self.session = secrets.token_urlsafe(32)
        self.csrf = secrets.token_urlsafe(32)


def byte_range(header, size):
    if header is None:
        return 0, size - 1, 200
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()) or size == 0:
        raise ValueError
    a, b = match.groups()
    start = int(a) if a else max(0, size - int(b))
    end = min(size - 1, int(b)) if a and b else size - 1
    if not 0 <= start <= end < size:
        raise ValueError
    return start, end, 206


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Request paths may include workspace titles. Do not persist access logs.
        pass

    def headers_for(self, status, mime, size, extra=None):
        self.send_response(status)
        defaults = {
            "Content-Type": mime,
            "Content-Length": str(size),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'",
        }
        for name, value in {**defaults, **(extra or {})}.items():
            self.send_header(name, value)
        self.end_headers()

    def json(self, obj, status=200, headers=None):
        body = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode()
        self.headers_for(status, "application/json; charset=utf-8", len(body), headers)
        if self.command != "HEAD":
            self.wfile.write(body)

    def process(self):
        try:
            host = self.headers.get("Host", "")
            allowed = {h + ":" + str(self.server.server_port) for h in ("localhost", "127.0.0.1")}
            if host not in allowed or (
                self.headers.get("Origin") and self.headers["Origin"] != "http://" + host
            ):
                raise Problem("仅允许本机同源访问。", 403)
            url = urlsplit(self.path)
            if url.scheme or url.netloc:
                raise Problem("请求目标无效。", 400)
            path = unquote(url.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if path.startswith("/api/"):
                if self.headers.get("Sec-Fetch-Site") in {"cross-site", "same-site"}:
                    raise Problem("仅允许同源请求。", 403)
                if path == "/api/bootstrap" and self.command in {"GET", "HEAD"}:
                    cookie = f"{self.server.cookie_name}={self.server.session}; Path=/; HttpOnly; SameSite=Strict"
                    return self.json(
                        {"csrf": self.server.csrf, "version": "1.0.0-alpha.2"},
                        headers={"Set-Cookie": cookie},
                    )
                cookies = SimpleCookie(self.headers.get("Cookie", ""))
                value = cookies.get(self.server.cookie_name)
                if not value or not hmac.compare_digest(value.value, self.server.session):
                    raise Problem("本机会话已失效，请刷新页面。", 401)
                if self.command == "POST":
                    if not hmac.compare_digest(self.headers.get("X-Echo-CSRF", ""), self.server.csrf):
                        raise Problem("写入校验失败，请刷新页面。", 403)
                    return self.post(path, query)
                return self.get(path, query)
            if self.command == "POST":
                raise Problem("未找到操作。", 404)
            asset = (self.server.ui / (path.lstrip("/") or "index.html")).resolve()
            if not asset.is_relative_to(self.server.ui) or not asset.is_file():
                raise Problem("未找到界面资源，请先构建前端。", 404)
            with asset.open("rb") as handle:
                return self.file(handle, asset.name, False)
        except Problem as exc:
            self.json({"error": str(exc)}, exc.status)
        except (ValueError, TypeError, KeyError, UnicodeError, CookieError):
            self.json({"error": "请求或文件格式无效。"}, 400)
        except (BrokenPipeError, ConnectionResetError):
            return
        except FileNotFoundError:
            self.json({"error": "本地文件缺失。"}, 404)
        except (OSError, sqlite3.Error):
            self.json({"error": "本地文件或运行依赖不可用。"}, 500)

    do_GET = do_HEAD = do_POST = process

    def length(self, maximum):
        value = self.headers.get("Content-Length", "")
        if (
            self.headers.get("Transfer-Encoding")
            or not value.isdigit()
            or len(value) > 12
            or int(value) > maximum
        ):
            raise Problem("请求体长度无效或过大。", 413)
        return int(value)

    def post(self, path, query):
        store = self.server.store
        if path == "/api/recordings":
            if self.headers.get_content_type().startswith("multipart/"):
                raise Problem("请直接上传媒体文件字节。", 415)
            rec = store.add_media(
                self.rfile,
                self.length(2 * 1024**3),
                unquote(self.headers.get("X-Echo-Filename", "")),
                query.get("title", ""),
                query.get("date", ""),
            )
            return self.json({"recording": rec}, 201)
        if self.headers.get_content_type() != "application/json":
            raise Problem("需要 JSON 请求体。", 415)
        maximum = 64 * 1024**2 if path.endswith("/transcripts/import") else 2 * 1024**2
        value = json.loads(self.rfile.read(self.length(maximum)))
        if not isinstance(value, dict):
            raise Problem("请求体必须是对象。")
        route = re.fullmatch(
            r"/api/recordings/([a-zA-Z0-9_-]+)/(transcripts/import|transcribe|minutes/import|minutes/revise)",
            path,
        )
        if not route:
            raise Problem("未找到操作。", 404)
        key, operation = route.groups()
        if operation == "transcripts/import":
            return self.json({"run": store.import_transcript(key, value)}, 201)
        if operation == "transcribe":
            return self.json({"job": asr.submit(store, key, self.server.model_dir)}, 202)
        if operation == "minutes/import":
            return self.json(store.import_minutes(key, value), 201)
        return self.json(store.append(key, value.get("expected_revision"), edits=value.get("edits")))

    def get(self, path, query):
        store = self.server.store
        if path == "/api/recordings":
            return self.json({"items": store.recordings()})
        route = re.fullmatch(
            r"/api/recordings/([a-zA-Z0-9_-]+)(?:/(runs/[a-zA-Z0-9_-]+|media|handoff|minutes))?", path
        )
        if not route:
            raise Problem("未找到资源。", 404)
        key, operation = route.groups()
        if operation is None:
            return self.json(store.detail(key))
        if operation.startswith("runs/"):
            return self.json(store.run(key, operation.split("/")[1]))
        if operation == "handoff":
            return self.json(store.handoff(key, query.get("run")))
        if operation == "minutes":
            revision = int(query["revision"]) if "revision" in query else None
            if revision is not None and not 0 < revision < 2**63:
                raise Problem("修订号无效。")
            return self.json(store.minutes(key, revision))
        row = store.recording_row(key)
        if "run" in query and not store.run(key, query["run"])["media_url"]:
            raise Problem("此版本与原媒体无法核对。", 409)
        with (store.assets / row["asset"]).open("rb") as handle:
            if not store.check_handle(handle, row["sha"]):
                raise Problem("媒体内容与登记哈希不符。", 409)
            return self.file(handle, row["asset"], True)

    def file(self, handle, name, ranges):
        size = handle.seek(0, 2)
        try:
            first, last, status = byte_range(self.headers.get("Range") if ranges else None, size)
        except ValueError:
            return self.headers_for(416, "text/plain", 0, {"Content-Range": f"bytes */{size}"})
        headers = {"Accept-Ranges": "bytes"} if ranges else {}
        if status == 206:
            headers["Content-Range"] = f"bytes {first}-{last}/{size}"
        length = max(0, last - first + 1)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self.headers_for(status, mime, length, headers)
        if self.command == "HEAD":
            return
        handle.seek(first)
        while length > 0:
            chunk = handle.read(min(length, 256 * 1024))
            if not chunk:
                break
            self.wfile.write(chunk)
            length -= len(chunk)
