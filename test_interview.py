import json
import os
from pathlib import Path
import tempfile
import unittest
import wave
from unittest.mock import patch

import interview as app


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "incoming"
        self.source.mkdir()
        self.data = patch.object(app, "DATA", root / "data")
        self.data.start()
        self.addCleanup(self.data.stop)
        self.audio = self.source / "面试.wav"
        with wave.open(str(self.audio), "wb") as w:
            w.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            w.writeframes(b"\x00\x00" * 16000)

    def scan(self):
        return app.scan(self.source, settle_seconds=0)

    @staticmethod
    def fake_engine(*args, **kwargs):
        return {"text": "Redis 缓存", "segments": [{"start": 0, "end": 1,
                "text": "Redis 缓存", "avg_logprob": -1.2}]}

    def test_append_change_and_delete_preserve_records(self):
        self.assertEqual(self.scan()["added"], 1)
        first = app.interviews()[0]
        self.assertEqual(self.scan()["unchanged"], 1)
        self.audio.write_bytes(self.audio.read_bytes() + b"\x00\x00")
        self.assertEqual(self.scan()["changed"], 1)
        self.assertEqual(app.interviews()[0]["id"], first["id"])
        (self.source / "第二场.wav").write_bytes(self.audio.read_bytes())
        self.assertEqual(self.scan()["added"], 1)
        self.audio.unlink()
        self.scan()
        self.assertEqual(len(app.interviews()), 2)

    def test_preview_full_force_and_notes_keep_history(self):
        self.scan()
        key = app.interviews()[0]["id"]
        preview = app.transcribe(key, limit=1, engine=self.fake_engine)
        again = app.transcribe(key, limit=1, engine=lambda *a, **k: self.fail("重复推理"))
        self.assertEqual(preview["id"], again["id"])
        full = app.transcribe(key, engine=self.fake_engine)
        newer = app.transcribe(key, force=True, engine=self.fake_engine)
        self.assertEqual(len({preview["id"], full["id"], newer["id"]}), 3)
        for run in app.runs(key):
            self.assertTrue(Path(run["output_dir"], "transcript.srt").is_file())
        app.add_note(key, "术语更正")
        app.add_note(key, "补充复盘")
        with app.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 2)

    def test_failure_is_recorded_and_retry_works(self):
        self.scan()
        key = app.interviews()[0]["id"]
        def fail(*args, **kwargs):
            raise RuntimeError("模拟模型失败")
        with self.assertRaises(RuntimeError):
            app.transcribe(key, engine=fail)
        self.assertEqual(app.runs(key)[0]["state"], "failed")
        self.assertEqual(app.transcribe(key, engine=self.fake_engine)["state"], "completed")
        self.assertEqual(len(app.runs(key)), 2)
        self.assertEqual(list(app.DATA.glob("audio-*.wav")), [])

    def test_changed_source_requires_scan(self):
        self.scan()
        key = app.interviews()[0]["id"]
        app.transcribe(key, engine=self.fake_engine)
        self.audio.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "源录屏已变化"):
            app.transcribe(key, engine=self.fake_engine)

    def test_new_files_must_settle(self):
        self.assertEqual(app.scan(self.source)["deferred"], 1)
        self.assertEqual(app.interviews(), [])

    def test_timestamp_carry(self):
        self.assertEqual(app.stamp(59.9996, True), "00:01:00,000")

    def test_silence_flags_and_timestamps_are_bounded(self):
        raw = {"segments": [{"start": 0, "end": 30, "text": "谢谢观看"}, {"start": 30, "end": 60, "text": "谢谢"}]}
        result = app.annotate_audio(raw, self.audio)
        self.assertEqual(result["segments"][0]["end"], 1)
        self.assertEqual(len(result["segments"]), 1)
        self.assertIn("音量极低，可能为静音幻觉", app.review_reasons(result["segments"][0]))
        self.assertEqual(len(result["raw_segments"]), 2)

    def test_review_regeneration_records_provenance(self):
        self.scan()
        key = app.interviews()[0]["id"]
        first = app.transcribe(key, engine=self.fake_engine)
        second = app.transcribe(key, reuse_run=first["id"])
        self.assertEqual(json.loads(second["options"])["based_on_run"], first["id"])
        self.assertNotEqual(first["id"], second["id"])

    def test_interrupted_worker_recovered_under_lock(self):
        self.scan()
        key = app.interviews()[0]["id"]
        with app.db() as c:
            c.execute("INSERT INTO runs(id,interview_id,source_sha256,model,options,state,started_at) VALUES(?,?,?,?,?,'running',?)",
                      ("old", key, "hash", app.MODEL, "{}", app.now()))
        with app.worker_lock():
            self.assertEqual(app.runs(key)[0]["state"], "interrupted")
            with self.assertRaisesRegex(RuntimeError, "已有转写任务"):
                with app.worker_lock():
                    pass


if __name__ == "__main__":
    unittest.main()
