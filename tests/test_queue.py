import unittest
from unittest.mock import Mock, patch
from echo import interview as core
from echo import queue_jobs as queue
from tests import test_interview as fixtures


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

    def test_revise_waiting_preserves_position_and_records_before_after(self):
        task = queue.submit(self.key, self.options)
        before = queue.jobs()[0]
        queue.revise(task, {**self.options, 'start': 15, 'limit': 30})
        after = queue.jobs()[0]
        self.assertEqual(before['seq'], after['seq'])
        runner = Mock(return_value={'id': 'result'})
        queue.process_one(runner)
        self.assertEqual(runner.call_args.kwargs['start'], 15)
        with core.db() as c:
            self.assertEqual(c.execute("SELECT count(*) FROM events WHERE kind='queue_updated'").fetchone()[0], 1)

    def test_revise_cannot_change_running_job(self):
        task = queue.submit(self.key, self.options)
        with queue.db() as c:
            c.execute("UPDATE queue_jobs SET state='running' WHERE id=?", (task,))
        with self.assertRaises(ValueError):
            queue.revise(task, {**self.options, 'limit': 30})
        self.assertEqual(queue.jobs()[0]['options'], __import__('json').dumps(self.options, sort_keys=True, ensure_ascii=False))

    def test_revise_rejects_duplicate(self):
        queue.submit(self.key, self.options)
        second = queue.submit(self.key, {**self.options, 'limit': 30})
        with self.assertRaises(ValueError):
            queue.revise(second, self.options)
