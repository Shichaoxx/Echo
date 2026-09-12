"""UI state checks use a temporary ledger; never start a real ASR worker."""
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from echo import interview as core
from echo import queue_jobs as queue
from tests import test_interview as fixtures


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
            app.radio(key="workspace_view").set_value("全局队列").run()
            next(b for b in app.button if b.label == "取消").click().run()
            self.assertEqual(queue.jobs()[0]["state"], "cancelled")
            self.assertEqual(core.runs(self.item["id"])[0]["state"], "running")

    def test_live_settings_validate_scope_and_retain_model_draft(self):
        app = AppTest.from_file(str(core.ROOT / 'app.py')).run()
        mode_key = 'mode-' + self.item['id']
        app.radio(key=mode_key).set_value('指定片段试转').run()
        self.assertTrue(any('起始位置已超出' in e.value for e in app.error))
        self.assertTrue(next(b for b in app.button if b.label == '开始本地转写').disabled)
        app.radio(key=mode_key).set_value('完整录屏').run()
        self.assertFalse(next(b for b in app.button if b.label == '开始本地转写').disabled)
        app.slider(key='chunk-' + self.item['id']).set_value(35).run()
        original = app.selectbox(key='asr_model').value
        whisper = next(k for k, v in core.MODELS.items() if v.backend != 'qwen')
        app.selectbox(key='asr_model').set_value(whisper).run()
        app.selectbox(key='asr_model').set_value(original).run()
        self.assertEqual(app.slider(key='chunk-' + self.item['id']).value, 35)
        self.assertFalse(app.exception)

    def test_theme_change_persists_and_review_external_update_renders(self):
        from echo import themes
        from echo import workspace_review as review
        app = AppTest.from_file(str(core.ROOT / 'app.py')).run()
        app.selectbox(key='appearance').set_value('midnight').run()
        self.assertEqual(themes.load_theme(core.DATA), 'midnight')
        self.assertTrue(any('--paper:#101720' in m.value for m in app.markdown))
        path = review.review_path(self.item['path'])
        path.write_text(review.template(self.item, []) + '\n## 独立复盘证据\nTest observation\n')
        app.button(key='refresh-review').click().run()
        self.assertTrue(any('独立复盘证据' in m.value for m in app.markdown))
        self.assertFalse(app.exception)

    def test_global_queue_is_separate_from_interview_and_draft_survives(self):
        with patch.object(queue, 'ensure_dispatcher'):
            app = AppTest.from_file(str(core.ROOT / 'app.py')).run()
            app.text_area(key='prompt-v2-' + self.item['id']).set_value('preserve terms').run()
            self.assertFalse(any('全局转写队列' in m.value for m in app.markdown))
            app.radio(key='workspace_view').set_value('全局队列').run()
            self.assertTrue(any('全局转写队列' in m.value for m in app.markdown))
            self.assertEqual(len(app.tabs), 0)
            app.radio(key='workspace_view').set_value('面试详情').run()
            self.assertEqual(app.text_area(key='prompt-v2-' + self.item['id']).value, 'preserve terms')
            self.assertFalse(app.exception)

    def test_search_empty_and_restore(self):
        app = AppTest.from_file(str(core.ROOT / 'app.py')).run()
        app.text_input(key='interview-search').set_value('不存在的公司').run()
        self.assertTrue(any('没有匹配的面试' in i.value for i in app.info))
        self.assertFalse(app.exception)
        app.text_input(key='interview-search').set_value('').run()
        self.assertEqual(app.session_state['selected_interview'], self.item['id'])
        self.assertFalse(app.exception)

    def test_profile_saves_and_remains_available_without_interviews(self):
        from echo import library
        with patch.object(core, 'interviews', return_value=[]):
            app = AppTest.from_file(str(core.ROOT / 'app.py')).run()
            app.radio(key='workspace_view').set_value('个人 Profile').run()
            app.text_input(key='profile-name').set_value('测试用户')
            app.text_input(key='profile-role').set_value('测试岗位')
            next(b for b in app.button if b.label == '保存个人资料').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(library.read_store(core.DATA, 'profile.json')['role'], '测试岗位')
            app = AppTest.from_file(str(core.ROOT / 'app.py'))
            app.session_state['workspace_view'] = '个人 Profile'
            app.run()
            self.assertEqual(app.text_input(key='profile-name').value, '测试用户')

    def test_archive_restore_and_view_preference_keep_source_and_id(self):
        from echo import library
        app = AppTest.from_file(str(core.ROOT / 'app.py')).run()
        app.radio(key='library-view').set_value('公司').run()
        self.assertEqual(app.session_state['selected_interview'], self.item['id'])
        self.assertEqual(library.read_store(core.DATA, 'library.json')['view'], '公司')
        app.button(key='archive-toggle-' + self.item['id']).click().run()
        self.assertFalse(app.exception)
        self.assertFalse(any(b.key == 'nav-' + self.item['id'] for b in app.button))
        app.selectbox(key='library-scope').set_value('已归档').run()
        self.assertEqual(app.session_state['selected_interview'], self.item['id'])
        app.button(key='archive-toggle-' + self.item['id']).click().run()
        app.selectbox(key='library-scope').set_value('进行中的面试').run()
        self.assertEqual(core.interviews()[0], self.item)
        self.assertFalse(app.exception)
