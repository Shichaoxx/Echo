"""UI state checks use a temporary ledger; never start a real ASR worker."""
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
import interview as core
import queue_jobs as queue
import test_interview as fixtures


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LedgerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.scan()
        queue.set_enabled(False)
        self.item = core.interviews()[0]

    def running(self):
        with core.db() as c:
            c.execute("INSERT INTO runs(id,interview_id,source_sha256,model,options,state,started_at) VALUES(?,?,?,?,?,'running',?)",
                      ("test-run", self.item["id"], self.item["sha256"], core.MODEL, '{"limit":0}', core.now()))
            c.execute("INSERT INTO progress VALUES(?,?,?,?,?)", ("test-run", "转写中", 4, 10, core.now()))

    def test_live_progress_disables_duplicate_submit(self):
        self.running()
        with patch.object(core, "worker_active", return_value=True):
            app = AppTest.from_file(str(core.ROOT / "app.py")).run()
        self.assertFalse(app.exception)
        self.assertTrue(next(b for b in app.button if b.label == "开始本地转写").disabled)
        self.assertTrue(any("已处理 4 / 10" in m.value for m in app.markdown))
        self.assertTrue(any("正在把对话" in m.value for m in app.markdown))

    def test_stopped_worker_allows_retry_and_does_not_fake_progress(self):
        self.running()
        with patch.object(core, "worker_active", return_value=False):
            app = AppTest.from_file(str(core.ROOT / "app.py")).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("进程已停止" in w.value for w in app.warning))
        self.assertFalse(next(b for b in app.button if b.label == "开始本地转写").disabled)

    def test_completed_result_renders_in_light_and_dark(self):
        core.transcribe(self.item["id"], engine=self.fixture.fake_engine)
        for theme in ("sage", "violet"):
            app = AppTest.from_file(str(core.ROOT / "app.py"))
            app.session_state["appearance"] = theme
            app.run()
            self.assertFalse(app.exception)
            self.assertTrue(any("最新转写已就绪" in m.value for m in app.markdown))

    def test_queue_accepts_submission_while_worker_is_busy(self):
        self.running()
        with patch.object(core, "worker_active", return_value=True), patch.object(queue, "ensure_dispatcher"):
            app = AppTest.from_file(str(core.ROOT / "app.py"))
            app.session_state["queue_mode"] = True
            app.run()
            button = next(b for b in app.button if b.label == "加入转写队列")
            self.assertFalse(button.disabled)
            button.click().run()
            self.assertFalse(app.exception)
            self.assertEqual(queue.jobs()[0]["state"], "queued")
            next(b for b in app.button if b.label == "取消").click().run()
            self.assertEqual(queue.jobs()[0]["state"], "cancelled")
            self.assertEqual(core.runs(self.item["id"])[0]["state"], "running")
