from datetime import datetime, timezone
import unittest
from task_status import duration_label, elapsed_seconds, estimate_remaining


class TaskStatusTests(unittest.TestCase):
    def test_eta_requires_observed_progress(self):
        self.assertIsNone(estimate_remaining(None, 10, 100, 50))
        self.assertIsNone(estimate_remaining((10, 0), 12, 100, 50))
        self.assertEqual(estimate_remaining((10, 0), 20, 100, 50), 400)
        self.assertIsNone(estimate_remaining((10, 0), 100, 100, 50))

    def test_elapsed_is_clamped_and_readable(self):
        self.assertEqual(duration_label(125), "02:05")
        self.assertEqual(duration_label(-1), "00:00")
        self.assertEqual(elapsed_seconds("2026-09-06T00:01:00+00:00", datetime(2026, 9, 6, tzinfo=timezone.utc)), 0)
