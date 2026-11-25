"""Regression tests for local recording identity, source-bound revisions and HTTP."""

import hashlib
import http.client
import io
import json
import subprocess
import sys
import tempfile
import threading
import unittest
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from server import asr
from server.http import Server
from server.store import Problem, Store


def audio_bytes():
    result = io.BytesIO()
    with wave.open(result, "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "uncompressed"))
        wav.writeframes(bytes(64000))
    return result.getvalue()


class DesktopLifecycle(unittest.TestCase):
    def test_closing_launcher_pipe_stops_the_bound_server(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            process = subprocess.Popen(
                [sys.executable, "-BEs", "-m", "server", "--data-dir", folder, "--port", "0",
                 "--exit-on-stdin-eof"],
                cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            )
            try:
                url = json.loads(process.stdout.readline())["url"]
                self.assertTrue(url.startswith("http://127.0.0.1:"))
                process.stdin.close()
                self.assertEqual(process.wait(timeout=5), 0)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                process.stdin.close()
                process.stdout.close()


class Workspace(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(tmp.name)
        self.raw = audio_bytes()
        self.rec = self.store.add_media(io.BytesIO(self.raw), len(self.raw), "fiction.wav")
        self.key = self.rec["id"]
        self.input = {
            "model": "test",
            "source_sha256": hashlib.sha256(self.raw).hexdigest(),
            "result": {
                "source_start": 1,
                "duration": 1,
                "segments": [
                    {"start": 1, "end": 1, "text": ""},
                    {"start": 1.25, "end": 1.8, "text": "Fictional answer."},
                ],
            },
        }
        self.run = self.store.import_transcript(self.key, self.input)
        self.packet = self.store.handoff(self.key, self.run["id"])
        self.doc = {
            **self.packet["template"],
            "blocks": [
                {
                    "id": "s",
                    "type": "summary",
                    "title": "Overview",
                    "text": "A fictional example.",
                    "evidence": [1],
                },
                {
                    "id": "c",
                    "type": "chapter",
                    "title": "Question",
                    "text": "The answer is fictional.",
                    "evidence": [1],
                },
                {
                    "id": "f",
                    "type": "followup",
                    "parent_id": "c",
                    "title": "Clarify",
                    "text": "Example question",
                    "evidence": [1],
                },
                {
                    "id": "q",
                    "type": "quote",
                    "title": "Excerpt",
                    "text": "Fictional answer.",
                    "interpretation": "Example.",
                    "evidence": [1],
                },
            ],
            "overview": {"title": "Map", "groups": [{"title": "Test", "block_ids": ["c"]}]},
            "comparison": {"title": "Overview", "block_ids": ["s", "c"]},
        }

    def test_immutable_versions_and_atomic_revision_conflicts(self):
        decoded = self.store.import_minutes(self.key, self.doc)["document"]
        self.assertTrue(decoded["verified"])
        self.assertEqual((decoded["scope"]["kind"], decoded["media_offset"]), ("partial", 0))
        self.assertEqual(decoded["blocks"][2]["parent_id"], "c")
        self.assertEqual(
            decoded["blocks"][0]["evidence"][0],
            {"index": 1, "start": 1.25, "end": 1.8, "text": "Fictional answer.", "playable": True},
        )
        other = self.store.import_transcript(self.key, self.input)
        self.assertNotEqual(other["id"], self.run["id"])

        def edit(value):
            try:
                self.store.append(self.key, 1, edits=[{"id": "s", "title": "Edited", "text": value}])
                return 200
            except Problem as exc:
                return exc.status

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(edit, ["One", "Two"])), [200, 409])
        with self.assertRaises(Problem) as stale:
            self.store.import_minutes(self.key, self.doc)
        self.assertEqual(stale.exception.status, 409)
        latest = Store(self.store.root).detail(self.key)
        self.assertEqual(latest["document"]["source_run_id"], self.run["id"])
        self.assertEqual(latest["document"]["author"], "user")
        self.assertEqual([r["revision"] for r in latest["history"]], [2, 1])
        self.assertEqual(self.store.minutes(self.key, 1)["document"], decoded)

    def test_only_trusted_runs_authorize_playback(self):
        untrusted = self.store.import_transcript(self.key, {**self.input, "source_sha256": ""})
        source = self.store.run(self.key, untrusted["id"])
        self.assertIsNone(source["media_url"])
        self.assertIn("哈希", source["warning"])
        with self.assertRaises(Problem):
            self.store.import_transcript(self.key, {**self.input, "source_sha256": "invalid"})
        with self.assertRaises(Problem):
            self.store.import_transcript(
                self.key,
                {
                    **self.input,
                    "result": {
                        **self.input["result"],
                        "segments": [{"start": 0, "end": 1, "text": "Out of scope"}],
                    },
                },
            )
        with self.assertRaises(Problem):
            self.store.run("another-recording", self.run["id"])
        self.store.import_minutes(self.key, self.doc)
        asset = self.store.assets / self.store.recording_row(self.key)["asset"]
        asset.write_bytes(b"changed")
        doc = self.store.minutes(self.key)["document"]
        self.assertTrue(doc["verified"])
        self.assertIsNone(doc["media_url"])
        self.assertFalse(doc["blocks"][0]["evidence"][0]["playable"])
        asset.unlink()
        self.assertFalse(self.store.minutes(self.key)["document"]["blocks"][0]["evidence"][0]["playable"])
        duplicate = self.store.add_media(io.BytesIO(self.raw), len(self.raw), "restored.wav")
        self.assertEqual(duplicate["id"], self.key)
        self.assertTrue(self.store.minutes(self.key)["document"]["blocks"][0]["evidence"][0]["playable"])

    def test_rejects_unbound_or_invented_content(self):
        for patch_ in (
            {"source_run_id": "absent"},
            {"transcript_sha256": "other"},
            {"comparison": {"title": "Bad", "block_ids": ["c", "missing"]}},
            {"blocks": [self.doc["blocks"][0], {**self.doc["blocks"][1], "evidence": [0]}]},
            {"blocks": [*self.doc["blocks"][:2], {**self.doc["blocks"][2], "parent_id": "s"}]},
            {"blocks": [*self.doc["blocks"][:3], {**self.doc["blocks"][3], "text": "unsourced"}]},
        ):
            with self.subTest(patch=patch_), self.assertRaises(Problem):
                self.store.import_minutes(self.key, {**self.doc, **patch_})
        self.assertIsNone(self.store.minutes(self.key)["document"])

    def test_invalid_reference_reports_exact_location_and_preserves_saved_revision(self):
        saved = self.store.import_minutes(self.key, self.doc)
        blocks = self.doc["blocks"]
        cases = [
            ({"blocks": [blocks[0], {**blocks[1], "evidence": [1, 91]}]}, "块 c 的 evidence[1]", "91"),
            (
                {"blocks": [blocks[0], {**blocks[1], "evidence": [1, 1]}]},
                "块 c 的 evidence[1]",
                "重复引用片段索引 1",
            ),
            (
                {"blocks": [*blocks[:3], {**blocks[3], "text": "Different wording."}]},
                "金句块 q",
                "片段 [1]",
            ),
            (
                {"overview": {"title": "Map", "groups": [{"title": "Test", "block_ids": ["missing"]}]}},
                "overview.groups[0].block_ids[0]",
                "missing",
            ),
            (
                {"comparison": {"title": "Table", "block_ids": ["c", "c"]}},
                "comparison.block_ids[1]",
                "重复引用了正文块 c",
            ),
        ]
        for change, location, detail in cases:
            with self.subTest(location=location, detail=detail):
                with self.assertRaises(Problem) as rejected:
                    self.store.import_minutes(self.key, {**self.doc, "expected_revision": 1, **change})
                self.assertIn(location, str(rejected.exception))
                self.assertIn(detail, str(rejected.exception))
                self.assertEqual(self.store.minutes(self.key), saved)

    def test_tampering_keeps_previous_snapshot_visible(self):
        self.store.import_minutes(self.key, self.doc)
        with self.store.db() as db:
            db.execute("UPDATE runs SET segments='{' WHERE id=?", (self.run["id"],))
        doc = self.store.detail(self.key)["document"]
        self.assertFalse(doc["verified"])
        self.assertEqual(doc["blocks"][0]["evidence"][0]["text"], "Fictional answer.")
        self.assertIsNone(doc["media_url"])

    def test_worker_recovers_abandoned_work_and_reports_native_failure(self):
        with self.store.db() as db:
            db.executemany(
                "INSERT INTO jobs(id,recording_id,state,created_at) VALUES(?,?,?,?)",
                [
                    ("interrupted", self.key, "running", "yesterday"),
                    ("queued", self.key, "queued", "today"),
                ],
            )
        with patch("server.asr.subprocess.run") as worker:
            worker.return_value.returncode = -9
            asr.drain(self.store, "/fictional/model")
        with self.store.db() as db:
            jobs = [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY id")]
        self.assertTrue(all(j["state"] == "failed" for j in jobs))
        self.assertIn("-9", jobs[1]["error"])

    def test_http_contract_and_guarded_byte_ranges(self):
        server = Server(self.store, 0, self.store.root / "ui")
        listener = threading.Thread(target=server.serve_forever, daemon=True)
        listener.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def request(method, path, body=None, headers=None):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                conn.request(method, path, body=body, headers=headers or {})
                response = conn.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                conn.close()

        status, h, bootstrap = request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        auth = {"Cookie": h["Set-Cookie"].split(";")[0]}
        write = {**auth, "X-Echo-CSRF": json.loads(bootstrap)["csrf"], "Content-Type": "application/json"}
        route = f"/api/recordings/{self.key}"
        self.assertEqual(request("GET", route)[0], 401)
        self.assertEqual(request("GET", "/api/bootstrap", headers={"Host": "untrusted.test"})[0], 403)
        self.assertEqual(request("POST", route + "/minutes/import", b"{}", auth)[0], 403)
        self.assertEqual(
            request("POST", route + "/minutes/import", b"{}", {**write, "Origin": "https://other.test"})[0],
            403,
        )
        self.assertEqual(
            request("POST", "/api/recordings", self.raw, {**write, "X-Echo-Filename": "sample.wav"})[0], 201
        )
        status, _, raw = request("POST", route + "/minutes/import", json.dumps(self.doc).encode(), write)
        self.assertEqual(status, 201, raw)
        uri = json.loads(raw)["document"]["media_url"]
        status, headers, raw = request("GET", uri, headers={**auth, "Range": "bytes=0-3"})
        self.assertEqual((status, raw), (206, b"RIFF"))
        self.assertEqual(headers["Content-Range"], f"bytes 0-3/{len(self.raw)}")
        self.assertEqual(request("GET", uri, headers={**auth, "Range": "bytes=9999999-"})[0], 416)
        self.assertEqual(request("HEAD", uri, headers=auth)[2], b"")
        with self.store.db() as db:
            db.execute("UPDATE runs SET segments='[]' WHERE id=?", (self.run["id"],))
        self.assertEqual(request("GET", uri, headers=auth)[0], 409)
