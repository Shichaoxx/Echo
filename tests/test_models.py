import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from echo import asr_models as models
from echo import interview as core
from tests import test_interview as fixtures


class ModelAdapterTests(unittest.TestCase):
    def test_chunk_boundaries_cover_all_samples(self):
        audio = np.ones(16000 * 97, dtype=np.float32)
        ranges = list(models.chunk_ranges(audio, 16000, 25))
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], len(audio))
        self.assertTrue(all(a[1] == b[0] for a, b in zip(ranges, ranges[1:])))
        self.assertTrue(all(0 < b - a <= 25 * 16000 for a, b in ranges))

    def test_qwen_resets_budget_and_preserves_timing(self):
        fake = Mock()
        fake.generate.return_value = SimpleNamespace(text="Hive Schema Linking", generation_tokens=20)
        progress = Mock()
        result = models.qwen_transcribe(fake, np.ones(16000 * 70, dtype=np.float32),
            rate=16000, language="zh", prompt="技术面试", chunk_seconds=25,
            silence_filter=True, progress=progress, max_tokens=1024)
        self.assertGreater(len(result["segments"]), 1)
        self.assertEqual(result["segments"][-1]["end"], 70)
        self.assertEqual(result["timestamp_kind"], "audio_chunk")
        for call in fake.generate.call_args_list:
            self.assertEqual(call.kwargs["language"], "Chinese")
            self.assertEqual(call.kwargs["system_prompt"], "技术面试")
            self.assertEqual(call.kwargs["max_tokens"], 1024)

    def test_silence_filter_is_optional_and_audited(self):
        fake = Mock()
        fake.generate.return_value = SimpleNamespace(text="", generation_tokens=1)
        args = dict(rate=16000, language="auto", prompt="", chunk_seconds=25, progress=Mock())
        silent = np.zeros(16000, dtype=np.float32)
        result = models.qwen_transcribe(fake, silent, silence_filter=True, **args)
        fake.generate.assert_not_called()
        self.assertEqual(len(result["skipped_regions"]), 1)
        empty = models.qwen_transcribe(fake, silent, silence_filter=False, **args)
        fake.generate.assert_called_once()
        self.assertIsNone(fake.generate.call_args.kwargs["language"])
        self.assertIn("空文本", empty["skipped_regions"][0]["reason"])

    def test_token_limit_fails_instead_of_silently_truncating(self):
        fake = Mock()
        fake.generate.return_value = SimpleNamespace(text="partial", generation_tokens=1024)
        with self.assertRaisesRegex(RuntimeError, "输出上限"):
            models.qwen_transcribe(fake, np.ones(16000, dtype=np.float32), rate=16000,
                language="zh", prompt="", chunk_seconds=25, silence_filter=False, progress=Mock())

    def test_model_preference_preserves_theme(self):
        with TemporaryDirectory() as d:
            p = Path(d)
            (p / "preferences.json").write_text(json.dumps({"theme": "forest"}))
            models.save_default_model(p, models.TURBO_MODEL)
            self.assertEqual(models.load_default_model(p), models.TURBO_MODEL)
            self.assertEqual(json.loads((p / "preferences.json").read_text())["theme"], "forest")

    def test_model_ready_requires_every_weight_shard(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            folder = root / models.MODELS[models.DEFAULT_MODEL].folder
            folder.mkdir()
            for name in ["config.json", "vocab.json", "merges.txt", "tokenizer_config.json"]:
                (folder / name).write_text("{}")
            (folder / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"a": "a.safetensors", "b": "b.safetensors"}}))
            (folder / "a.safetensors").touch()
            self.assertFalse(models.model_ready(models.DEFAULT_MODEL, root))
            (folder / "b.safetensors").touch()
            self.assertTrue(models.model_ready(models.DEFAULT_MODEL, root))


class BackendLedgerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LedgerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.scan()
        self.key = core.interviews()[0]["id"]

    def test_changing_model_creates_new_version(self):
        qwen = core.transcribe(self.key, engine=self.fixture.fake_engine)
        whisper = core.transcribe(self.key, model=models.TURBO_MODEL, engine=self.fixture.fake_engine)
        self.assertNotEqual(qwen["id"], whisper["id"])
        self.assertEqual(json.loads(qwen["options"])["precision"], "BF16")
        self.assertEqual(json.loads(whisper["options"])["precision"], "FP16")

    def test_clip_timestamp_offset_and_provenance(self):
        # Source contains one second; test a half-second clip with model-relative timing.
        run = core.transcribe(self.key, start=0.5, engine=self.fixture.fake_engine)
        data = json.loads(Path(run["output_dir"], "transcript.json").read_text())
        result = data["result"]
        self.assertEqual(result["source_start"], 0.5)
        self.assertEqual(result["segments"][0]["start"], 0.5)
        self.assertAlmostEqual(result["segments"][0]["end"], 1.0, places=2)
        self.assertEqual(result["raw_segments"][0]["start"], 0)
        regenerated = core.transcribe(self.key, start=0.5, reuse_run=run["id"])
        again = json.loads(Path(regenerated["output_dir"], "transcript.json").read_text())["result"]
        self.assertEqual(again["segments"][0]["start"], 0.5)

    def test_unsupported_model_and_invalid_start_rejected(self):
        for options in ({"model": "made-up"}, {"start": -1}, {"chunk_seconds": 0}):
            with self.assertRaises(ValueError):
                core.transcribe(self.key, engine=self.fixture.fake_engine, **options)

    def test_engine_receives_selected_backend(self):
        with patch.object(core, "transcribe_audio", return_value=self.fixture.fake_engine()) as engine:
            core.transcribe(self.key, model=models.TURBO_MODEL, language="en")
            self.assertEqual(engine.call_args.kwargs["key"], models.TURBO_MODEL)
            self.assertEqual(engine.call_args.kwargs["language"], "en")


if __name__ == "__main__":
    unittest.main()
