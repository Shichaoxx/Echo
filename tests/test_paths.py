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
        self.assertEqual(json.loads(result.stdout)["output"], str(self.data / "outputs"))
        self.assertTrue((self.data / "paths.json").is_file())
