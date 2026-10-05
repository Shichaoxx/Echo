"""Regression coverage for workspace moves and long-lived workers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from echo import paths, interview as core, queue_jobs as queue


class PathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / "data"
        self.source = self.root / "recordings"
        self.source.mkdir()
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_deleted_workspace_is_not_recreated_by_idle_dispatcher(self):
        workspace = self.root / "workspace"
        workspace.mkdir()
        data = workspace / "data"
        with patch.object(core, "DATA", data):
            self.assertFalse(queue.process_one())
            workspace.rename(self.root / "moved")
            with self.assertRaises(FileNotFoundError):
                queue.process_one()
        self.assertFalse(workspace.exists())

    def test_mirrored_default_and_custom_output(self):
        paths.save_directories(self.data, self.source, self.root / 'ignored', default_output=True)
        media = self.source / 'Company' / 'Round one' / 'recording.mov'
        self.assertEqual(paths.output_directory(self.data), self.source / 'echo')
        self.assertEqual(paths.mirrored_output(self.data, media, paths.output_directory(self.data)), self.source / 'echo/Company/Round one')
        custom = self.root / 'custom'
        paths.save_directories(self.data, self.source, custom)
        self.assertEqual(paths.mirrored_output(self.data, media, custom), custom / 'Company/Round one')
        self.assertEqual(paths.mirrored_output(self.data, self.source / 'recording.mov', custom), custom)
        with self.assertRaises(ValueError):
            paths.save_directories(self.data, self.source, self.source)

    def test_new_workspace_requires_an_explicit_folder_even_if_recordings_exists(self):
        with patch.object(paths, "ROOT", self.root), patch.object(core, "DATA", self.data):
            self.assertIsNone(paths.source_directory(self.data, required=False))
            with self.assertRaisesRegex(ValueError, "请先选择面试文件夹"):
                core.scan(settle_seconds=0)
        self.assertFalse(self.data.exists())

    def test_unselected_workspace_can_resolve_individually_imported_outputs(self):
        media = self.source / "interview.mov"
        self.assertEqual(paths.output_directory(self.data), self.data / "outputs")
        self.assertEqual(paths.mirrored_output(self.data, media, self.data / "outputs"),
                         self.data / "outputs" / self.source.name)
        self.assertFalse(paths.media_in_source(self.data, media))

    def test_active_folder_membership_excludes_outputs_and_similar_prefixes(self):
        paths.save_directories(self.data, self.source, self.source / "generated")
        self.assertTrue(paths.media_in_source(self.data, self.source / "Company/interview.mov"))
        for path in (self.root / "recordings-other/interview.mov", self.source / "generated/audio.mp3",
                     self.source / "echo/audio.mp3", self.source / "echos/audio.mp3"):
            self.assertFalse(paths.media_in_source(self.data, path), str(path))

    def test_scan_excludes_generated_audio(self):
        paths.save_directories(self.data, self.source, self.source / 'generated')
        for relative in ['original.wav', 'generated/Company/audio.mp3', 'echo/Company/audio.mp3', 'echos/Company/audio.mp3']:
            target = self.source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'synthetic')
        with patch.object(core, 'DATA', self.data):
            self.assertEqual(core.scan(settle_seconds=0)['added'], 1)

    def test_scan_excludes_ledger_audio_inside_selected_folder(self):
        data = self.source / ".local"
        paths.save_directories(data, self.source, self.source / "echo", default_output=True)
        (self.source / "original.wav").write_bytes(b"synthetic recording")
        (data / "audio-active-run.wav").write_bytes(b"synthetic derived audio")
        with patch.object(core, "DATA", data):
            self.assertEqual(core.scan(settle_seconds=0)["added"], 1)
            self.assertEqual(len(core.interviews()), 1)

    def test_ledger_at_source_root_does_not_hide_original_media(self):
        paths.save_directories(self.source, self.source, self.source / "echo", default_output=True)
        media = self.source / "original.wav"
        media.write_bytes(b"synthetic recording")
        self.assertTrue(paths.media_in_source(self.source, media))
        with patch.object(core, "DATA", self.source):
            self.assertEqual(core.scan(settle_seconds=0)["added"], 1)

    def test_deleted_data_root_is_not_silently_reinitialized(self):
        paths.ensure_data_directory(self.data)
        self.data.rmdir()
        with self.assertRaises(FileNotFoundError):
            paths.ensure_data_directory(self.data)
        self.assertFalse(self.data.exists())

    def test_dangling_link_does_not_create_old_parent(self):
        project = self.root / "project"
        project.mkdir()
        target = self.root / "old" / "data"
        (project / ".local").symlink_to(target, target_is_directory=True)
        with self.assertRaises(FileNotFoundError):
            paths.ensure_data_directory(paths.data_directory(project))
        self.assertFalse(target.parent.exists())

    def test_saved_relative_paths_follow_containing_workspace(self):
        paths.save_directories(self.data, self.source, self.root / "results")
        saved = json.loads((self.data / "paths.json").read_text())
        self.assertEqual(saved["source"], "../recordings")
        moved = self.root.with_name(self.root.name + "-moved")
        self.root.rename(moved)
        try:
            self.assertEqual(paths.source_directory(moved / "data"), moved / "recordings")
            self.assertEqual(paths.output_directory(moved / "data"), moved / "results")
        finally:
            moved.rename(self.root)

    def test_running_child_reads_updated_output_and_inherits_data_root(self):
        paths.save_directories(self.data, self.source, self.root / "first")
        env = paths.child_environment(self.data)
        paths.save_directories(self.data, self.source, self.root / "second")
        result = subprocess.run([sys.executable, "interview.py", "paths"], cwd=core.ROOT,
                                env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["output"], str(self.root / "second"))

    def test_env_override_cannot_be_saved_as_different_value(self):
        with patch.dict(os.environ, {"INTERVIEW_OUTPUT_DIR": str(self.root / "env-results")}):
            with self.assertRaises(ValueError):
                paths.save_directories(self.data, self.source, self.root / "ignored")
        self.assertFalse(self.data.exists())

    def test_legacy_input_still_works(self):
        paths.ensure_data_directory(self.data)
        (self.data / "source_path.txt").write_text(str(self.source))
        self.assertEqual(paths.source_directory(self.data), self.source)

    def test_empty_legacy_input_is_not_a_folder_selection(self):
        paths.ensure_data_directory(self.data)
        (self.data / "source_path.txt").write_text("\n")
        self.assertIsNone(paths.source_directory(self.data, required=False))
        self.assertEqual(paths.output_directory(self.data), self.data / "outputs")

    def test_dangling_link_with_existing_parent_is_not_repaired_silently(self):
        project = self.root / "project"
        project.mkdir()
        target = self.root / "missing-data"
        (project / ".local").symlink_to(target, target_is_directory=True)
        with self.assertRaises(FileNotFoundError):
            paths.ensure_data_directory(paths.data_directory(project))
        self.assertFalse(target.exists())

    def test_first_cli_configuration_creates_default_data_and_output_settings(self):
        env = {**os.environ, "INTERVIEW_DATA_DIR": str(self.data)}
        result = subprocess.run([sys.executable, "interview.py", "paths", "--source", str(self.source)],
                                cwd=core.ROOT, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["output"], str(self.source / "echo"))
        self.assertTrue((self.data / "paths.json").is_file())

    def test_cli_reports_no_selection_without_creating_a_data_folder(self):
        env = {**os.environ, "INTERVIEW_DATA_DIR": str(self.data)}
        result = subprocess.run([sys.executable, "interview.py", "paths"],
                                cwd=core.ROOT, env=env, capture_output=True, text=True, check=True)
        self.assertIsNone(json.loads(result.stdout)["source"])
        self.assertFalse(self.data.exists())

    def test_cli_source_switch_keeps_default_or_custom_output_mode(self):
        other = self.root / "other"
        other.mkdir()
        env = {**os.environ, "INTERVIEW_DATA_DIR": str(self.data)}
        for default in (True, False):
            with self.subTest(default=default):
                custom = self.root / "custom-results"
                paths.save_directories(self.data, self.source, custom, default_output=default)
                result = subprocess.run([sys.executable, "interview.py", "paths", "--source", str(other)],
                                        cwd=core.ROOT, env=env, capture_output=True, text=True, check=True)
                expected = other / "echo" if default else custom
                self.assertEqual(json.loads(result.stdout)["output"], str(expected))

    def test_cli_output_without_a_selected_source_fails_without_saving(self):
        env = {**os.environ, "INTERVIEW_DATA_DIR": str(self.data)}
        result = subprocess.run([sys.executable, "interview.py", "paths", "--output", str(self.root / "results")],
                                cwd=core.ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("请先选择面试文件夹", result.stderr)
        self.assertFalse(self.data.exists())
