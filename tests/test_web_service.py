"""Real loopback HTTP checks against temporary, synthetic-only workspaces."""
import http.client
import json
import os
from pathlib import Path
import select
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import wave

from echo import interview as core, queue_jobs as queue
from echo.web_service import WebService, WebHTTPServer, ROOT


class WebServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "workspace"
        self.source = self.root / "recordings"
        self.source.mkdir()
        self.media = self.source / "synthetic.wav"
        with wave.open(str(self.media), "wb") as f:
            f.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            f.writeframes(b"\0" * 32000)
        self.ui = self.root / "ui"
        self.ui.mkdir()
        (self.ui / "index.html").write_text("<!doctype html><title>Echo synthetic test</title>")
        (self.ui / "app.js").write_text("'use strict';")
        (self.ui / "private.txt").write_text("not a UI asset")
        old_data = core.DATA
        self.addCleanup(setattr, core, "DATA", old_data)
        env = patch.dict(os.environ, {"INTERVIEW_SOURCE_DIR": "", "INTERVIEW_OUTPUT_DIR": ""})
        env.start()
        self.addCleanup(env.stop)
        self.service = WebService(self.data, self.ui)
        duration = patch.object(self.service, "duration", return_value=2.0)
        duration.start()
        self.addCleanup(duration.stop)
        runtime = patch.object(self.service, "runtime", return_value={"python": "test-runtime", "python_version": "3.9", "platform": "test", "ffmpeg": True, "ffprobe": True, "backends": {"qwen": True, "whisper": True}})
        runtime.start()
        self.addCleanup(runtime.stop)
        self.server = WebHTTPServer(self.service)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.stop_server, thread)
        self.port = self.server.server_port
        self.csrf = None
        self.cookie = None
        self.bootstrap()
        status, _, result = self.request("POST", "/api/import", {"paths": [str(self.media)]})
        self.assertEqual(status, 200, result)
        self.key = result["selected_id"]

    def test_project_routes_persist_and_detach_without_deleting_record(self):
        status, _, result = self.request('POST', '/api/projects', {'operation':'create','name':'Test project'})
        self.assertEqual(status,200,result)
        key = next(iter(result['projects']))
        route = f'/api/interviews/{self.key}/metadata'
        self.assertEqual(self.request('POST',route,{'project_id':key})[0],200)
        snapshot = self.request('GET','/api/library')[2]
        self.assertEqual(snapshot['items'][0]['metadata']['project_id'],key)
        self.assertEqual(snapshot['projects'][key]['name'],'Test project')
        self.assertEqual(self.request('POST','/api/projects',{'operation':'delete','id':key})[0],200)
        snapshot = self.request('GET','/api/library')[2]
        self.assertEqual(snapshot['count'],1)
        self.assertEqual(snapshot['items'][0]['metadata']['project_id'],'')
        self.assertTrue(self.media.exists())

    def test_waveform_silence_cache_and_authentication(self):
        route = f"/api/interviews/{self.key}/waveform"
        self.assertEqual(self.request("GET", route, authenticated=False)[0], 401)
        status, _, result = self.request("GET", route)
        self.assertEqual(status, 200, result)
        self.assertEqual(result["duration"], 2)
        self.assertTrue(0 < len(result["peaks"]) <= 512)
        self.assertEqual(max(result["peaks"]), 0)
        with patch("echo.web_service.subprocess.run", side_effect=AssertionError("cache missed")):
            self.assertEqual(self.request("GET", route)[0], 200)



    def test_waveform_busy_missing_decoder_and_changed_media(self):
        route = f"/api/interviews/{self.key}/waveform"
        self.service.waveform_lock.acquire()
        try:
            self.assertEqual(self.request("GET", route)[0], 503)
        finally:
            self.service.waveform_lock.release()
        with patch("echo.web_service.shutil.which", return_value=None):
            self.assertEqual(self.request("GET", route)[0], 422)
        self.service.waveform(self.media)
        with wave.open(str(self.media), "wb") as f:
            f.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
            f.writeframes(b"\x00\x40" * 8000)
        envelope = self.service.waveform(self.media)
        self.assertEqual(envelope["duration"], 1)
        self.assertAlmostEqual(max(envelope["peaks"]), 0.5)

    def test_directory_picker_lists_only_folders_without_changing_settings(self):
        (self.source / "中文 文件夹").mkdir()
        (self.source / ".hidden").mkdir()
        (self.source / "linked").symlink_to(self.source / "中文 文件夹", target_is_directory=True)
        before = self.service.settings()
        status, _, result = self.request("POST", "/api/directories", {"path": str(self.source)})
        self.assertEqual(status, 200)
        self.assertEqual(result["path"], str(self.source.resolve()))
        self.assertEqual(result["parent"], str(self.source.resolve().parent))
        self.assertEqual({item["name"] for item in result["directories"]}, {"中文 文件夹", "linked"})
        self.assertEqual(self.service.settings(), before)
        status, _, result = self.request("POST", "/api/directories", {"path": str(self.source), "hidden": True})
        self.assertEqual(status, 200)
        self.assertIn(".hidden", [item["name"] for item in result["directories"]])

    def test_fresh_settings_require_choice_and_folder_switch_preserves_history(self):
        self.assertEqual(self.service.settings()["source"], "")
        self.assertFalse(self.service.settings()["source_configured"])
        self.assertEqual(self.request("POST", "/api/scan", {})[0], 400)
        route = f"/api/interviews/{self.key}"
        # Imported media is playable before any ASR version exists.
        details = self.request("GET", route)[2]
        self.assertIsNone(details["run"])
        self.assertEqual(self.request("GET", details["media_url"])[2], self.media.read_bytes())
        core.add_note(self.key, "Synthetic preserved note")
        for folder, count in ((self.source, 1), (self.root / "other", 0), (self.source, 1)):
            folder.mkdir(exist_ok=True)
            status, _, result = self.request("POST", "/api/settings", {
                "source": str(folder), "output": str(folder / "echo"), "output_mode": "default"})
            self.assertEqual(status, 200, result)
            self.assertTrue(result["settings"]["source_configured"])
            self.assertEqual(self.request("GET", "/api/library")[2]["count"], count)
        self.assertEqual(self.request("GET", route)[2]["notes"][0]["body"], "Synthetic preserved note")
        self.assertTrue(self.media.is_file())

    def test_selected_folder_rejects_imports_from_other_folders(self):
        other = self.root / "other"
        other.mkdir()
        self.request("POST", "/api/settings", {"source": str(other), "output": str(other / "echo")})
        status, _, result = self.request("POST", "/api/import", {"paths": [str(self.media)]})
        self.assertEqual(status, 400)
        self.assertIn("切换目录", result["error"])

    def test_active_tasks_prevent_switching_their_output_destination(self):
        settings = {"source": str(self.source), "output": str(self.source / "echo"), "output_mode": "default"}
        self.assertEqual(self.request("POST", "/api/settings", settings)[0], 200)
        other = self.root / "other"
        other.mkdir()
        changed = {**settings, "source": str(other)}
        with patch.object(queue, "jobs", return_value=[{"state": "queued"}]):
            self.assertEqual(self.request("POST", "/api/settings", settings)[0], 200)
            self.assertEqual(self.request("POST", "/api/settings", changed)[0], 409)
            self.assertEqual(self.request("POST", "/api/settings", {**settings, "output_layout": "company"})[0], 409)
        with patch.object(core, "worker_active", return_value=True):
            self.assertEqual(self.request("POST", "/api/settings", changed)[0], 409)
        self.assertEqual(self.service.settings()["source"], str(self.source.resolve()))

    def test_memory_init_is_explicit_and_rejects_stale_library_or_output(self):
        self.assertEqual(self.request('GET', '/api/memory', authenticated=False)[0], 401)
        self.assertEqual(self.request('POST', '/api/memory/init', {})[0], 400)
        self.assertEqual(self.request('POST', '/api/settings', {
            'source': str(self.source), 'output': str(self.source / 'echo'), 'output_mode': 'default'})[0], 200)
        status, _, state = self.request('GET', '/api/memory')
        self.assertEqual(status, 200, state)
        self.assertFalse(state['initialized'])
        self.assertFalse(Path(state['root']).exists())
        expected = {key: state[key] for key in ('source_directory', 'root')}
        self.assertEqual(self.request('POST', '/api/memory/init', {**expected, 'root': '/stale'})[0], 409)
        self.assertFalse(Path(state['root']).exists())
        status, _, initialized = self.request('POST', '/api/memory/init', expected)
        self.assertEqual(status, 200, initialized)
        self.assertTrue(initialized['initialized'])
        self.assertTrue(Path(initialized['index_path']).is_file())
        handoff = self.request('GET', f'/api/interviews/{self.key}/handoff')[2]
        self.assertEqual(handoff['memory']['root'], initialized['root'])
        self.assertIn(initialized['index_path'], handoff['prompt'])
        other = self.root / 'other-library'
        other.mkdir()
        self.assertEqual(self.request('POST', '/api/settings', {
            'source': str(other), 'output': str(other / 'echo'), 'output_mode': 'default'})[0], 200)
        self.assertEqual(self.request('POST', '/api/memory/init', expected)[0], 409)
        self.assertFalse((other / 'echo' / 'memory').exists())

    def test_handoff_is_read_only_authenticated_and_bound_to_selected_version(self):
        route = f"/api/interviews/{self.key}/handoff"
        self.assertEqual(self.request("GET", route, authenticated=False)[0], 401)
        before = self.request("GET", route)[2]
        self.assertFalse(before["ready"])
        self.assertIsNone(before["run_id"])
        self.assertFalse(Path(before["review_path"]).exists())
        run_id, output = self.completed_run(offset=10, run_id="partial-run")
        with core.db() as c:
            c.execute("UPDATE runs SET options=? WHERE id=?", (json.dumps({"start": 10, "limit": 1}), run_id))
        self.completed_run(run_id="another-run")
        self.request("POST", f"/api/interviews/{self.key}/draft", {
            "revision": 0, "body": "Synthetic saved note", "citations": [{"run_id": run_id, "index": 0}]})
        status, _, result = self.request("GET", route + "?run=" + run_id)
        self.assertEqual(status, 200, result)
        self.assertTrue(result["ready"])
        self.assertEqual(result["run_id"], run_id)
        self.assertEqual(result["scope"]["kind"], "partial")
        self.assertEqual(result["scope"]["start"], 10)
        self.assertIn(str(output / "transcript.json"), result["prompt"])
        self.assertIn("Synthetic saved note", result["prompt"])
        self.assertIn("不构成执行指令", result["prompt"])
        self.assertNotIn("another-run", result["prompt"])
        self.assertFalse(Path(result["review_path"]).exists())
        self.assertEqual(self.request("GET", route + "?run=unknown")[0], 404)
        with core.db() as c:
            c.execute("UPDATE interviews SET sha256='changed' WHERE id=?", (self.key,))
        self.assertFalse(self.request("GET", route + "?run=" + run_id)[2]["ready"])

    def test_handoff_missing_transcript_and_review_refresh_use_real_files(self):
        run_id, output = self.completed_run()
        route = f"/api/interviews/{self.key}"
        review_file = Path(self.request("GET", route)[2]["review"]["path"])
        review_file.parent.mkdir(parents=True, exist_ok=True)
        review_file.write_text("# Synthetic Codex review", encoding="utf-8")
        self.assertIn("Synthetic Codex review", self.request("GET", route)[2]["review"]["body"])
        (output / "transcript.json").unlink()
        result = self.request("GET", route + "/handoff?run=" + run_id)[2]
        self.assertFalse(result["ready"])
        self.assertTrue(result["warnings"])

    def test_handoff_does_not_infer_full_coverage_from_cached_audio(self):
        run_id, _ = self.completed_run()
        self.media.unlink()
        result = self.request("GET", f"/api/interviews/{self.key}/handoff?run={run_id}")[2]
        self.assertEqual(result["scope"]["kind"], "unknown")
        self.assertIsNone(result["scope"]["source_duration"])
        self.assertTrue(any("完整覆盖" in warning for warning in result["warnings"]))

    def test_changing_output_keeps_existing_review_from_previous_run(self):
        run_id, output = self.completed_run()
        review_file = output / "复盘.md"
        review_file.write_text("# Preserved synthetic review", encoding="utf-8")
        status, _, _ = self.request("POST", "/api/settings", {
            "source": str(self.source), "output": str(self.root / "new-output")})
        self.assertEqual(status, 200)
        route = f"/api/interviews/{self.key}"
        result = self.request("GET", route + "/handoff?run=" + run_id)[2]
        self.assertEqual(Path(result["review_path"]), review_file)
        self.assertIn("Preserved synthetic review", self.request("GET", route)[2]["review"]["body"])
        self.assertFalse((self.root / "new-output").exists())

    def test_directory_picker_errors_and_authentication(self):
        self.assertEqual(self.request("POST", "/api/directories", {"path": str(self.source)}, authenticated=False)[0], 401)
        self.assertEqual(self.request("POST", "/api/directories", {"path": str(self.source)}, headers={"X-Echo-CSRF": "invalid"})[0], 403)
        for value, expected in [("relative/path", 400), (str(self.media), 400), (str(self.root / "missing"), 404)]:
            self.assertEqual(self.request("POST", "/api/directories", {"path": value})[0], expected)
        self.assertFalse((self.root / "missing").exists())
        status, _, result = self.request("POST", "/api/directories", {"path": str(self.root / "missing" / "output"), "initial": True})
        self.assertEqual(status, 200)
        self.assertEqual(result["path"], str(self.root.resolve()))
        self.assertTrue(result["notice"])
        self.assertFalse((self.root / "missing").exists())
        with patch("echo.web_service.os.scandir", side_effect=PermissionError):
            status, _, result = self.request("POST", "/api/directories", {"path": str(self.source)})
        self.assertEqual(status, 422)
        self.assertIn("权限", result["error"])

    def stop_server(self, thread):
        self.server.shutdown()
        self.server.server_close()
        thread.join(2)

    def request(self, method, path, value=None, headers=None, authenticated=True):
        common = {"Host": f"localhost:{self.port}"}
        if authenticated and self.cookie:
            common["Cookie"] = self.cookie
        if method == "POST":
            common.update({"Content-Type": "application/json", "Origin": f"http://localhost:{self.port}"})
            if authenticated and self.csrf:
                common["X-Echo-CSRF"] = self.csrf
        common.update(headers or {})
        body = json.dumps(value) if value is not None else None
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=common)
            response = connection.getresponse()
            data = response.read()
            response_headers = dict(response.getheaders())
            if "application/json" in response_headers.get("Content-Type", "") and data:
                data = json.loads(data)
            return response.status, response_headers, data
        finally:
            connection.close()

    def bootstrap(self):
        status, headers, data = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.csrf = data["csrf"]
        self.cookie = headers.get("Set-Cookie", self.cookie).split(";")[0]
        return headers, data

    def completed_run(self, *, offset=0, run_id="synthetic-run"):
        output = self.data / "outputs" / self.key / run_id
        core.export_result({"segments": [{"start": offset, "end": offset + 1, "text": "这是虚构的测试内容。"}],
                            "text": "这是虚构的测试内容。", "duration": 1, "source_start": offset}, output,
                           {"title": "合成示例", "model": core.MODEL, "scope": "synthetic"})
        (output / "audio.mp3").write_bytes(b"synthetic-mp3-fixture-not-for-playback")
        with core.db() as c:
            item = c.execute("SELECT * FROM interviews WHERE id=?", (self.key,)).fetchone()
            c.execute("INSERT INTO runs(id,interview_id,source_sha256,model,options,state,started_at,finished_at,output_dir) VALUES(?,?,?,?,?,'completed',?,?,?)",
                      (run_id, self.key, item["sha256"], core.MODEL, "{}", core.now(), core.now(), str(output)))
        return run_id, output

    def test_preferred_version_and_resume_routes_validate_and_persist(self):
        first, _ = self.completed_run(run_id='first')
        second, _ = self.completed_run(run_id='second')
        route = f'/api/interviews/{self.key}/preferred-run'
        self.assertEqual(self.request('POST', route, {'run_id': first})[0], 200)
        details = self.request('GET', f'/api/interviews/{self.key}')[2]
        self.assertEqual(details['run']['id'], first)
        self.assertEqual(details['preferred_run_id'], first)
        self.assertEqual(self.request('POST', route, {'run_id': 'other-interview-run'})[0], 400)
        self.assertEqual(self.request('POST', '/api/navigation', {'interview_id': self.key, 'run_id': second, 'position': 1.2})[0], 200)
        snapshot = self.request('GET', '/api/overview')[2]
        self.assertEqual(snapshot['navigation']['position'], 1.2)
        self.assertEqual(self.request('POST', '/api/navigation', {'interview_id': self.key, 'position': -5})[0], 400)
        self.assertEqual(self.request('GET', '/api/material?id=../../outside')[0], 400)
        self.assertEqual(self.request('GET', '/api/overview', authenticated=False)[0], 401)

    def test_bootstrap_cookie_and_private_api_access(self):
        headers, body = self.request("GET", "/api/bootstrap", authenticated=False)[1:]
        self.assertIn("HttpOnly", headers["Set-Cookie"])
        self.assertIn("SameSite=Strict", headers["Set-Cookie"])
        self.assertGreaterEqual(len(body["csrf"]), 32)
        self.assertNotEqual(body["csrf"], self.csrf)
        self.assertIn("runtime", body)
        self.assertEqual(self.request("GET", "/api/library", authenticated=False)[0], 401)
        self.assertEqual(self.request("GET", "/")[0], 200)
        self.assertEqual(self.request("GET", "/api/library")[2]["count"], 1)

    def test_health_is_minimal_and_same_origin_protected(self):
        status, _, health = self.request("GET", "/api/health", authenticated=False)
        self.assertEqual(status, 200)
        self.assertEqual(health, {"status": "ok", "version": self.service.version})
        self.assertEqual(self.request("HEAD", "/api/health", authenticated=False)[0], 200)
        self.assertEqual(self.request("GET", "/api/health", headers={"Host": "attacker.invalid"}, authenticated=False)[0], 403)
        self.assertEqual(self.request("GET", "/api/health", headers={"Origin": "https://attacker.invalid"}, authenticated=False)[0], 403)

    def test_rejects_dns_rebinding_cross_origin_and_bad_csrf(self):
        for headers in ({"Host": "attacker.invalid"}, {"Origin": "https://attacker.invalid"},
                        {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request("GET", "/api/bootstrap", headers=headers)[0], 403)
        self.assertEqual(self.request("POST", "/api/scan", {}, {"X-Echo-CSRF": "wrong"})[0], 403)
        self.assertEqual(self.request("POST", "/api/scan", {}, {"Content-Type": "text/plain"})[0], 415)

    def test_allowlisted_assets_and_registered_media_only(self):
        for path in ("/private.txt", "/../private.txt", "/%2e%2e/private.txt", "/api/files?path=/etc/passwd"):
            self.assertEqual(self.request("GET", path)[0], 404)
        (self.ui / "escaped.js").symlink_to(self.root / "outside.js")
        (self.root / "outside.js").write_text("outside")
        self.assertEqual(self.request("GET", "/escaped.js")[0], 404)
        status, headers, data = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("text/javascript", headers["Content-Type"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

    def test_media_head_range_suffix_and_unsatisfiable(self):
        endpoint = f"/api/interviews/{self.key}/media"
        raw = self.media.read_bytes()
        status, headers, body = self.request("HEAD", endpoint)
        self.assertEqual(status, 200)
        self.assertEqual(int(headers["Content-Length"]), len(raw))
        self.assertEqual(body, b"")
        status, headers, body = self.request("GET", endpoint, headers={"Range": "bytes=4-13"})
        self.assertEqual(status, 206)
        self.assertEqual(body, raw[4:14])
        self.assertEqual(headers["Content-Range"], f"bytes 4-13/{len(raw)}")
        self.assertEqual(self.request("GET", endpoint, headers={"Range": "bytes=-5"})[2], raw[-5:])
        for range_ in ("bytes=999999-", "bytes=12-3", "bytes=0-1,4-5", "bytes=-0"):
            self.assertEqual(self.request("GET", endpoint, headers={"Range": range_})[0], 416)
        self.assertEqual(self.request("GET", "/api/interviews/unknown/media")[0], 404)

    def test_drafts_are_append_only_conflict_checked_and_citations_authoritative(self):
        run, _ = self.completed_run()
        core.add_note(self.key, "v1 笔记必须保留")
        endpoint = f"/api/interviews/{self.key}/draft"
        payload = {"body": "第一版", "revision": 0, "citations": [{"run_id": run, "index": 0, "text": "不能伪造引用", "start": 99}]}
        status, _, body = self.request("POST", endpoint, payload)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["draft"]["revision"], 1)
        self.assertEqual(body["draft"]["citations"][0]["start"], 0)
        self.assertEqual(body["draft"]["citations"][0]["text"], "这是虚构的测试内容。")
        stale = self.request("POST", endpoint, {**payload, "body": "旧窗口"})
        self.assertEqual(stale[0], 409)
        self.assertEqual(stale[2]["draft"]["body"], "第一版")
        self.assertEqual(self.request("POST", endpoint, {**payload, "revision": 1, "body": "第二版"})[0], 200)
        with core.db() as c:
            self.assertEqual([r[0] for r in c.execute("SELECT body FROM desktop_drafts ORDER BY revision")], ["第一版", "第二版"])
            self.assertEqual(c.execute("SELECT body FROM notes").fetchone()[0], "v1 笔记必须保留")
        invalid = {**payload, "revision": 2, "citations": [{"run_id": "wrong-run", "index": 0}]}
        self.assertEqual(self.request("POST", endpoint, invalid)[0], 400)

    def test_draft_survives_service_restart_and_missing_result(self):
        run, output = self.completed_run()
        endpoint = f"/api/interviews/{self.key}/draft"
        first = self.request("POST", endpoint, {"body": "保留", "revision": 0, "citations": [{"run_id": run, "index": 0}]})[2]["draft"]
        replacement = WebService(self.data, self.ui)
        self.assertEqual(replacement.draft(self.key), first)
        (output / "transcript.json").unlink()
        saved = self.request("POST", endpoint, {**first, "body": "结果丢失后仍可追加"})
        self.assertEqual(saved[0], 200, saved[2])
        self.assertEqual(saved[2]["draft"]["citations"], first["citations"])

    def test_selected_run_offsets_and_cached_media_fallback(self):
        run, output = self.completed_run(offset=10)
        endpoint = f"/api/interviews/{self.key}?run={run}"
        details = self.request("GET", endpoint)[2]
        self.assertEqual(details["media_offset"], 0)
        self.assertEqual(details["segments"][0]["start"], 10)
        self.assertEqual(details["cached_media_offset"], 10)
        self.assertEqual(self.request("GET", details["cached_media_url"])[2], (output / "audio.mp3").read_bytes())
        self.media.unlink()
        details = self.request("GET", endpoint)[2]
        self.assertEqual(details["media_offset"], 10)
        self.assertFalse(details["source_exists"])
        self.assertEqual(details["duration"], 11)
        self.assertEqual(self.request("GET", f"/api/interviews/{self.key}?run=unknown")[0], 404)

    def test_queue_validates_parameters_deduplicates_and_cancels(self):
        endpoint = "/api/queue"
        options = {"model": core.MODEL, "language": "zh"}
        with patch.object(queue, "ensure_dispatcher") as dispatcher:
            request = {"interview_id": self.key, "options": options}
            first = self.request("POST", endpoint, request)
            self.assertEqual(first[0], 201, first[2])
            second = self.request("POST", endpoint, request)
            self.assertEqual(first[2]["id"], second[2]["id"])
            self.assertEqual(dispatcher.call_count, 2)
            for bad in ({"start": 3}, {"limit": 4}, {"start": float("nan")}, {"force": "yes"}, {"chunk_seconds": 9}, {"extra": 2}):
                self.assertEqual(self.request("POST", endpoint, {**request, "options": {**options, **bad}})[0], 400)
            task = first[2]["id"]
            snapshot = self.request("GET", endpoint)[2]
            self.assertEqual(snapshot["jobs"][0]["options"]["model"], core.MODEL)
            self.assertIn("title", snapshot["jobs"][0])
            self.assertEqual(self.request("POST", f"/api/queue/{task}/cancel", {})[0], 200)
            self.assertEqual(self.request("POST", f"/api/queue/{task}/cancel", {})[0], 409)

    def test_optional_duration_accepts_null_omission_and_zero(self):
        with patch.object(queue, "ensure_dispatcher"):
            for start in (0, 1):
                task_ids = []
                for duration in ({}, {"limit": None}, {"limit": 0}):
                    status, _, result = self.request("POST", "/api/queue", {
                        "interview_id": self.key, "options": {"start": start, **duration}})
                    self.assertEqual(status, 201, result)
                    self.assertEqual(result["job"]["options"]["limit"], 0)
                    self.assertEqual(result["job"]["options"]["start"], start)
                    task_ids.append(result["id"])
                self.assertEqual(len(set(task_ids)), 1)
            for invalid in (-1, True, "", "abc", float("inf")):
                status, _, result = self.request("POST", "/api/queue", {
                    "interview_id": self.key, "options": {"limit": invalid}})
                self.assertEqual(status, 400, result)
            self.assertEqual(len(queue.jobs()), 2)

    def test_missing_runtime_does_not_enqueue(self):
        with patch.object(self.service, "runtime", return_value={"ffmpeg": False, "ffprobe": False, "backends": {"qwen": False, "whisper": False}}), patch.object(queue, "ensure_dispatcher") as dispatcher:
            self.assertEqual(self.request("POST", "/api/queue", {"interview_id": self.key, "options": {}})[0], 400)
            self.assertEqual(queue.jobs(), [])
            dispatcher.assert_not_called()

    def test_malformed_result_returns_json_error_without_losing_connection(self):
        run, output = self.completed_run()
        for malformed in ([], {"result": {"segments": ["invalid"]}},
                          {"result": {"segments": [{"start": -1, "end": 0, "text": "invalid"}]}},
                          {"result": {"segments": [], "duration": "unknown"}}):
            (output / "transcript.json").write_text(json.dumps(malformed))
            response = self.request("GET", f"/api/interviews/{self.key}?run={run}")
            self.assertEqual(response[0], 422, response[2])
            self.assertIn("error", response[2])

    def test_settings_metadata_and_export_preserve_v1_data(self):
        output = self.root / "results"
        status, _, value = self.request("POST", "/api/settings", {"source": str(self.source), "output": str(output)})
        self.assertEqual(status, 200, value)
        self.assertEqual(value["settings"]["source"], str(self.source.resolve()))
        endpoint = f"/api/interviews/{self.key}/metadata"
        self.assertEqual(self.request("POST", endpoint, {"company": "虚构组织", "date": "2026-01-02", "archived": True})[0], 200)
        self.assertEqual(self.request("POST", endpoint, {"archived": "yes"})[0], 400)
        self.assertEqual(self.request("POST", endpoint, {"path": "/tmp/other.wav"})[0], 400)
        self.completed_run()
        core.add_note(self.key, "合成历史笔记")
        self.request("POST", f"/api/interviews/{self.key}/draft", {"body": "合成草稿", "revision": 0, "citations": []})
        status, headers, body = self.request("GET", f"/api/interviews/{self.key}/export?format=md")
        self.assertEqual(status, 200)
        self.assertIn("合成草稿", body.decode())
        self.assertIn("合成历史笔记", body.decode())
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(self.request("GET", f"/api/interviews/{self.key}/export?format=srt")[0], 200)
        self.assertEqual(self.request("GET", f"/api/interviews/{self.key}/export?format=../secret")[0], 404)

    def test_deleted_data_directory_is_not_recreated(self):
        shutil.rmtree(self.data)
        self.assertEqual(self.request("GET", "/api/library")[0], 503)
        self.assertEqual(self.request("GET", "/api/bootstrap")[0], 503)
        status, _, health = self.request("GET", "/api/health", authenticated=False)
        self.assertEqual(status, 503)
        self.assertEqual(health, {"status": "unavailable", "version": self.service.version})
        self.assertFalse(self.data.exists())

    def test_replaced_data_directory_is_not_reported_healthy(self):
        self.data.rename(self.root / "moved-workspace")
        self.data.mkdir()
        self.assertEqual(self.request("GET", "/api/health", authenticated=False)[0], 503)
        self.assertEqual(self.request("GET", "/api/bootstrap")[0], 503)
        self.assertEqual(list(self.data.iterdir()), [])

    def test_import_does_not_copy_files_and_rejects_invalid_media(self):
        prior = self.media.read_bytes()
        result = self.request("POST", "/api/import", {"paths": [str(self.media)]})
        self.assertEqual(result[2]["selected_id"], self.key)
        self.assertEqual(self.media.read_bytes(), prior)
        self.assertEqual(len(core.interviews()), 1)
        self.assertFalse((self.data / "synthetic.wav").exists())
        for invalid in ([str(self.ui / "private.txt")], ["relative.wav"], []):
            self.assertEqual(self.request("POST", "/api/import", {"paths": invalid})[0], 400)


class WebCLITests(unittest.TestCase):
    """Subprocess lifecycle checks; no repository/user workspace is opened."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "existing-workspace"
        self.data.mkdir()
        self.ui = self.root / "ui"
        self.ui.mkdir()
        (self.ui / "index.html").write_text("<!doctype html><title>Synthetic CLI fixture</title>")
        self.env = {**os.environ, "INTERVIEW_DATA_DIR": str(self.data),
                    "INTERVIEW_SOURCE_DIR": "", "INTERVIEW_OUTPUT_DIR": "",
                    "PYTHONDONTWRITEBYTECODE": "1"}

    def command(self, *args):
        return [sys.executable, str(ROOT / "workbench.py"), "--ui-dir", str(self.ui), *args]

    @staticmethod
    def stop(process):
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            return process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return process.communicate(timeout=5)

    def launch(self, *args):
        process = subprocess.Popen(self.command(*args), cwd=ROOT, env=self.env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, process)
        readable, _, _ = select.select([process.stdout], [], [], 10)
        if not readable:
            self.stop(process)
            self.fail("The local CLI did not report startup within ten seconds")
        line = process.stdout.readline()
        self.assertTrue(line, "CLI exited before reporting startup")
        record = json.loads(line)
        self.assertEqual(set(record), {"url", "pid", "version"})
        url = urlsplit(record["url"])
        self.assertEqual((url.scheme, url.hostname, url.path), ("http", "127.0.0.1", "/"))
        self.assertEqual(record["pid"], process.pid)
        return process, record, url.port

    def test_cli_first_line_url_existing_workspace_bootstrap_and_clean_shutdown(self):
        marker = self.data / "retained.txt"
        marker.write_text("synthetic preexisting file")
        process, record, port = self.launch("--data-dir", str(self.data), "--port", "0")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", "/api/bootstrap")
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["version"], record["version"])
            self.assertEqual(Path(payload["settings"]["data"]).resolve(), self.data.resolve())
            self.assertIn("HttpOnly", response.getheader("Set-Cookie"))
        finally:
            connection.close()
        stdout, stderr = self.stop(process)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertEqual(stdout, "")
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(marker.read_text(), "synthetic preexisting file")

    def test_cli_default_data_directory_uses_environment(self):
        process, _, port = self.launch("--port", "0")
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("GET", "/api/health")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["status"], "ok")
        finally:
            connection.close()
        self.assertTrue((self.data / "ledger.sqlite3").is_file())
        self.stop(process)

    def test_cli_occupied_port_has_clear_error_without_success_url(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            result = subprocess.run(self.command("--port", str(occupied.getsockname()[1])),
                                    cwd=ROOT, env=self.env, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("已被占用", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_cli_missing_parent_and_invalid_settings_fail_without_success_url(self):
        missing = self.root / "missing-parent" / "workspace"
        result = subprocess.run(self.command("--data-dir", str(missing)), cwd=ROOT,
                                env=self.env, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("启动失败", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(missing.parent.exists())
        (self.data / "paths.json").write_text("not valid JSON")
        result = subprocess.run(self.command(), cwd=ROOT, env=self.env,
                                text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("启动失败", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
