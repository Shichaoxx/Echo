import unittest
from unittest.mock import Mock, patch
import interview as core
import queue_jobs as queue
import test_interview as fixtures


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LedgerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.scan()
        self.key = core.interviews()[0]["id"]
        self.options = dict(model=core.MODEL, language="zh", start=0, limit=0,
                            chunk_seconds=25, silence_filter=True, prompt="test", force=False)

    def test_fifo_and_deduplication_preserve_options(self):
        first = queue.submit(self.key, self.options)
        self.assertEqual(first, queue.submit(self.key, self.options))
        second = queue.submit(self.key, {**self.options, "limit": 30})
        runner = Mock(return_value={"id": "result"})
        queue.process_one(runner)
        self.assertEqual(runner.call_args.kwargs["limit"], 0)
        queue.process_one(runner)
        self.assertEqual(runner.call_args.kwargs["limit"], 30)
        self.assertEqual([j["id"] for j in queue.jobs()], [first, second])
        self.assertTrue(all(j["state"] == "completed" for j in queue.jobs()))

    def test_existing_worker_is_never_interrupted(self):
        queue.submit(self.key, self.options)
        runner = Mock()
        with patch.object(core, "worker_active", return_value=True):
            self.assertFalse(queue.process_one(runner))
        runner.assert_not_called()
        self.assertEqual(queue.jobs()[0]["state"], "queued")

    def test_busy_race_requeues_and_failure_does_not_block_next(self):
        queue.submit(self.key, self.options)
        queue.submit(self.key, {**self.options, "limit": 30})
        self.assertFalse(queue.process_one(Mock(side_effect=core.WorkerBusy())))
        self.assertEqual(queue.jobs()[0]["state"], "queued")
        queue.process_one(Mock(side_effect=ValueError("bad audio")))
        queue.process_one(Mock(return_value={"id": "next"}))
        self.assertEqual([j["state"] for j in queue.jobs()], ["failed", "completed"])

    def test_cancel_and_preference_do_not_delete_history(self):
        task = queue.submit(self.key, self.options)
        queue.set_enabled(False)
        self.assertEqual(queue.jobs()[0]["state"], "queued")
        queue.cancel(task)
        self.assertEqual(queue.jobs()[0]["state"], "cancelled")
        self.assertFalse(queue.process_one(Mock()))

    def test_source_change_requires_resubmission(self):
        queue.submit(self.key, self.options)
        with core.db() as c:
            c.execute("UPDATE interviews SET sha256='changed' WHERE id=?", (self.key,))
        runner = Mock()
        queue.process_one(runner)
        runner.assert_not_called()
        self.assertEqual(queue.jobs()[0]["state"], "failed")
