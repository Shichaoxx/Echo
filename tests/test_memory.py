"""Memory checks use only temporary folders and synthetic notes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from echo import memory, paths
from echo.codex_handoff import build_handoff


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.data = self.root / "data"
        self.data.mkdir()
        self.source = self.root / "interviews"
        self.source.mkdir()
        self.output = self.root / "outputs"
        environment = patch.dict(os.environ, {"INTERVIEW_SOURCE_DIR": "", "INTERVIEW_OUTPUT_DIR": ""})
        environment.start()
        self.addCleanup(environment.stop)
        paths.save_directories(self.data, self.source, self.output)

    def files(self):
        return {str(p.relative_to(self.root)): (p.stat().st_mtime_ns, p.read_bytes())
                for p in self.root.rglob("*") if p.is_file()}

    def assert_error(self, code, function, *args):
        with self.assertRaises(memory.MemoryStorageError) as raised:
            function(*args)
        self.assertEqual(raised.exception.code, code)

    def test_status_and_handoff_before_initialization_do_not_write(self):
        before = self.files()
        result = memory.status(self.data)
        self.assertFalse(result["initialized"])
        self.assertEqual(result["root"], str(self.output / "memory"))
        handoff = self.handoff()
        self.assertEqual(handoff["memory"], result)
        self.assertIn('"memory"', handoff["prompt"])
        self.assertIn("docs/memory.md", handoff["prompt"])
        self.assertIn("经用户确认后", handoff["prompt"])
        self.assertEqual(self.files(), before)
        self.assertFalse(self.output.exists())

    def test_initialization_is_idempotent_and_preserves_manual_edits(self):
        result = memory.initialize(self.data)
        self.assertTrue(result["initialized"])
        root = Path(result["root"])
        self.assertEqual(set(p.name for p in root.iterdir()), {"MEMORY.md", "profile.md", "topics", memory.OWNER})
        self.assertEqual(list((root / "topics").iterdir()), [])
        self.assertIn("待用户确认", Path(result["profile_path"]).read_text())
        Path(result["profile_path"]).write_text("Synthetic manually confirmed profile\n")
        (root / "topics" / "synthetic.md").write_text("Synthetic topic\n")
        before = self.files()
        self.assertEqual(memory.initialize(self.data), result)
        self.assertEqual(memory.status(self.data), result)
        self.assertEqual(self.files(), before)
        locations = json.loads((self.data / memory.BINDINGS).read_text())
        self.assertEqual(locations, {"schema_version": 1, "locations": {str(self.source): str(root)}})

    def test_output_changes_preserve_the_original_bound_location(self):
        original = memory.initialize(self.data)
        alternate = self.root / "alternate"
        paths.save_directories(self.data, self.source, alternate)
        self.assertEqual(memory.status(self.data), original)
        self.assertEqual(memory.initialize(self.data), original)
        self.assertFalse(alternate.exists())

    def test_sources_have_separate_bindings(self):
        first = memory.initialize(self.data)
        other = self.root / "other"
        other.mkdir()
        paths.save_directories(self.data, other, other / "echo", default_output=True)
        second = memory.initialize(self.data)
        self.assertNotEqual(first["root"], second["root"])
        self.assertEqual(second["source_directory"], str(other))
        paths.save_directories(self.data, self.source, self.root / "different-output")
        self.assertEqual(memory.status(self.data), first)
        self.assertEqual(len(json.loads((self.data / memory.BINDINGS).read_text())["locations"]), 2)

    def test_shared_output_for_another_source_is_rejected(self):
        memory.initialize(self.data)
        other = self.root / "other"
        other.mkdir()
        paths.save_directories(self.data, other, self.output)
        before = self.files()
        self.assert_error("location_conflict", memory.status, self.data)
        self.assert_error("location_conflict", memory.initialize, self.data)
        self.assertEqual(self.files(), before)

    def test_missing_bound_directory_is_not_recreated(self):
        result = memory.initialize(self.data)
        root = Path(result["root"])
        root.rename(self.output / "moved-memory")
        self.assert_error("bound_directory_missing", memory.status, self.data)
        self.assert_error("bound_directory_missing", memory.initialize, self.data)
        self.assertFalse(root.exists())

    def test_missing_bound_file_is_not_silently_replaced(self):
        result = memory.initialize(self.data)
        profile = Path(result["profile_path"])
        profile.unlink()
        self.assert_error("incomplete_memory", memory.status, self.data)
        self.assert_error("incomplete_memory", memory.initialize, self.data)
        self.assertFalse(profile.exists())

    def test_unowned_existing_directory_is_not_adopted(self):
        root = self.output / "memory"
        root.mkdir(parents=True)
        (root / "manual.md").write_text("Synthetic existing work")
        before = self.files()
        self.assert_error("unowned_directory", memory.status, self.data)
        self.assert_error("unowned_directory", memory.initialize, self.data)
        self.assertEqual(self.files(), before)
        self.assertFalse((root / memory.OWNER).exists())

    def test_owner_of_another_source_or_data_root_is_rejected(self):
        result = memory.initialize(self.data)
        marker = Path(result["root"]) / memory.OWNER
        owner = json.loads(marker.read_text())
        owner["source_directory"] = str(self.root / "other")
        marker.write_text(json.dumps(owner))
        self.assert_error("location_conflict", memory.status, self.data)

    def test_symlink_roots_and_managed_entries_are_rejected(self):
        result = memory.initialize(self.data)
        root = Path(result["root"])
        relocated = self.output / "relocated"
        root.rename(relocated)
        root.symlink_to(relocated, target_is_directory=True)
        self.assert_error("unsafe_path", memory.status, self.data)
        self.assert_error("unsafe_path", memory.initialize, self.data)
        root.unlink()
        relocated.rename(root)
        profile = root / "profile.md"
        profile.rename(root / "saved-profile.md")
        profile.symlink_to(root / "saved-profile.md")
        self.assert_error("unsafe_path", memory.status, self.data)
        profile.unlink()
        (root / "saved-profile.md").rename(profile)
        (root / "topics" / "linked.md").symlink_to(profile)
        self.assert_error("unsafe_path", memory.status, self.data)

    def test_binding_and_lock_symlinks_are_rejected(self):
        target = self.root / "binding-target"
        target.write_text("{}")
        (self.data / memory.BINDINGS).symlink_to(target)
        self.assert_error("unsafe_path", memory.status, self.data)
        self.assert_error("unsafe_path", memory.initialize, self.data)
        (self.data / memory.BINDINGS).unlink()
        (self.data / "memory-locations.lock").symlink_to(target)
        self.assert_error("unsafe_path", memory.initialize, self.data)
        self.assertEqual(target.read_text(), "{}")
        self.assertFalse(self.output.exists())

    def test_no_source_does_not_initialize_or_create_a_lock(self):
        (self.data / "paths.json").unlink()
        before = self.files()
        result = memory.status(self.data)
        self.assertFalse(result["initialized"])
        self.assertIsNone(result["source_directory"])
        self.assertIsNone(result["root"])
        self.assert_error("source_required", memory.initialize, self.data)
        self.assertEqual(self.files(), before)
        self.assertFalse((self.data / "memory-locations.lock").exists())

    def test_unavailable_data_source_and_output_are_explicit(self):
        self.assert_error("data_unavailable", memory.status, self.root / "missing-data")
        self.assert_error("data_unavailable", memory.initialize, self.root / "missing-data")
        self.source.rmdir()
        self.assert_error("source_unavailable", memory.status, self.data)
        self.source.mkdir()
        self.output.write_text("Synthetic non-directory")
        self.assert_error("output_unavailable", memory.status, self.data)

    def test_valid_data_symlink_is_compatible_with_existing_echo_workspaces(self):
        alias = self.root / ".local"
        alias.symlink_to(self.data, target_is_directory=True)
        result = memory.initialize(alias)
        self.assertEqual(memory.status(self.data), result)
        self.data.rename(self.root / "moved-data")
        self.assert_error("data_unavailable", memory.status, alias)

    def test_malformed_binding_is_not_overwritten(self):
        binding = self.data / memory.BINDINGS
        binding.write_text("not JSON")
        self.assert_error("invalid_binding", memory.initialize, self.data)
        self.assertEqual(binding.read_text(), "not JSON")

    def test_atomic_binding_failure_keeps_old_bindings_and_can_be_retried(self):
        first = memory.initialize(self.data)
        old = (self.data / memory.BINDINGS).read_bytes()
        other = self.root / "other"
        other.mkdir()
        paths.save_directories(self.data, other, other / "echo", default_output=True)
        with patch("echo.memory.os.replace", side_effect=OSError("synthetic replacement failure")):
            self.assert_error("io_error", memory.initialize, self.data)
        self.assertEqual((self.data / memory.BINDINGS).read_bytes(), old)
        self.assertFalse(memory.status(self.data)["initialized"])
        second = memory.initialize(self.data)
        self.assertTrue(second["initialized"])
        self.assertTrue(Path(first["root"]).is_dir())
        self.assertEqual(len(json.loads((self.data / memory.BINDINGS).read_text())["locations"]), 2)

    def test_concurrent_cli_initializers_keep_both_source_bindings(self):
        other = self.root / "other"
        other.mkdir()
        command = [sys.executable, "-m", "echo.memory", "init", "--data-dir", str(self.data)]
        processes = []
        for source in (self.source, other):
            env = {**os.environ, "INTERVIEW_SOURCE_DIR": str(source), "INTERVIEW_OUTPUT_DIR": str(source / "echo")}
            processes.append(subprocess.Popen(command, cwd=paths.ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertTrue(json.loads(stdout)["initialized"])
        locations = json.loads((self.data / memory.BINDINGS).read_text())["locations"]
        self.assertEqual(set(locations), {str(self.source), str(other)})

    def test_configuration_change_while_waiting_for_lock_does_not_initialize_another_source(self):
        other = self.root / "other"
        other.mkdir()
        original_lock = memory._lock

        def switch_before_lock(data):
            paths.save_directories(data, other, other / "echo", default_output=True)
            return original_lock(data)

        with patch("echo.memory._lock", side_effect=switch_before_lock):
            self.assert_error("configuration_changed", memory.initialize, self.data)
        self.assertFalse(self.output.exists())
        self.assertFalse((other / "echo").exists())

    def test_expected_source_and_root_are_checked_under_the_initialization_lock(self):
        for expected in ({"expected_source": str(self.root / "other")}, {"expected_root": str(self.root / "elsewhere")}):
            with self.subTest(expected=expected):
                with self.assertRaises(memory.MemoryStorageError) as raised:
                    memory.initialize(self.data, **expected)
                self.assertEqual(raised.exception.code, "configuration_changed")
                self.assertFalse(self.output.exists())
        status = memory.status(self.data)
        result = memory.initialize(self.data, expected_source=status["source_directory"], expected_root=status["root"])
        self.assertTrue(result["initialized"])

    def test_handoff_reads_only_status_not_memory_bodies(self):
        result = memory.initialize(self.data)
        Path(result["profile_path"]).write_text("Synthetic private marker that must not be copied into a handoff")
        before = self.files()
        handoff = self.handoff()
        self.assertTrue(handoff["memory"]["initialized"])
        self.assertEqual(handoff["memory"], result)
        self.assertNotIn("Synthetic private marker", handoff["prompt"])
        self.assertEqual(self.files(), before)

    def test_unavailable_memory_does_not_block_single_interview_handoff(self):
        result = memory.initialize(self.data)
        shutil.rmtree(result["root"])
        handoff = self.handoff()
        self.assertEqual(handoff["memory"]["error"], "bound_directory_missing")
        self.assertIsNone(handoff["memory"]["root"])
        self.assertEqual(handoff["review_path"], str(self.output / "复盘.md"))
        self.assertIn("已绑定的 Memory 目录", handoff["prompt"])
        self.assertFalse(Path(result["root"]).exists())

    def test_handoff_does_not_attach_another_source_memory(self):
        memory.initialize(self.data)
        handoff = self.handoff(media=self.root / "other" / "recording.wav")
        self.assertEqual(handoff["memory"]["error"], "source_mismatch")
        self.assertIsNone(handoff["memory"]["index_path"])

    def handoff(self, media=None):
        details = {"item": {"id": "synthetic", "title": "Synthetic interview", "path": str(media or self.source / "recording.wav"), "sha256": "synthetic-sha"},
                   "run": None, "review": {"path": str(self.output / "复盘.md"), "exists": False}}
        return build_handoff(details, {"data": str(self.data), "source": str(self.source), "output": str(self.output)}, paths.ROOT)


if __name__ == "__main__":
    unittest.main()
