"""UI state checks use a temporary ledger; never start a real ASR worker."""
import os
from pathlib import Path
import shutil
import time
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest
from echo import interview as core
from echo import queue_jobs as queue
from echo import paths
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

    def test_directory_save_scan_and_new_output_preserve_history(self):
        previous = core.transcribe(self.item["id"], engine=self.fixture.fake_engine)
        previous_output = Path(previous["output_dir"])
        incoming = self.fixture.source.parent.resolve() / "new recordings"
        incoming.mkdir()
        recording = incoming / "另一场.wav"
        recording.write_bytes(self.fixture.audio.read_bytes())
        os.utime(recording, (time.time() - 120, time.time() - 120))
        destination = self.fixture.source.parent.resolve() / "new transcripts"

        app = AppTest.from_file(str(core.ROOT / "app.py")).run()
        app.text_input(key="source-directory").set_value(str(incoming)).run()
        self.assertTrue(next(b for b in app.button if b.label == "扫描新增录屏").disabled)
        app.text_input(key="output-directory").set_value(str(destination))
        with patch.object(core, "scan") as scan:
            app.button(key="save-directories").click().run()
            scan.assert_not_called()
        self.assertFalse(app.exception)
        self.assertTrue(any("历史结果保留当前位置" in message.value for message in app.success))

        app = AppTest.from_file(str(core.ROOT / "app.py")).run()
        self.assertEqual(app.text_input(key="source-directory").value, str(incoming))
        self.assertEqual(app.text_input(key="output-directory").value, str(destination))
        next(b for b in app.button if b.label == "扫描新增录屏").click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(core.interviews()), 2)
        imported = next(item for item in core.interviews() if item["path"] == str(recording))
        current = core.transcribe(imported["id"], engine=self.fixture.fake_engine)
        self.assertTrue(Path(current["output_dir"]).is_relative_to(destination))
        self.assertEqual(core.runs(self.item["id"])[0]["output_dir"], str(previous_output))
        self.assertTrue((previous_output / "transcript.json").is_file())

    def test_invalid_directory_save_keeps_current_preferences(self):
        destination = self.fixture.source.parent.resolve() / "transcripts"
        paths.save_directories(core.DATA, str(self.fixture.source), str(destination))
        missing = self.fixture.source.parent / "missing" / "recordings"
        app = AppTest.from_file(str(core.ROOT / "app.py")).run()
        app.text_input(key="source-directory").set_value(str(missing))
        app.button(key="save-directories").click().run()
        self.assertFalse(app.exception)
        self.assertTrue(app.error)
        self.assertEqual(paths.source_directory(core.DATA), self.fixture.source.resolve())
        self.assertEqual(paths.output_directory(core.DATA), destination)
        self.assertFalse(missing.parent.exists())

    def test_missing_data_parent_shows_error_without_recreating_it(self):
        missing_parent = self.fixture.source.parent / "removed-project"
        with patch.object(core, "DATA", missing_parent / "data"), \
                patch.object(core, "interviews") as interviews, \
                patch.object(queue, "ensure_dispatcher") as dispatcher:
            app = AppTest.from_file(str(core.ROOT / "app.py")).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("无法访问本地数据目录" in error.value for error in app.error))
        self.assertFalse(missing_parent.exists())
        interviews.assert_not_called()
        dispatcher.assert_not_called()

    def test_deleted_active_data_directory_stops_the_next_render(self):
        app = AppTest.from_file(str(core.ROOT / "app.py")).run()
        shutil.rmtree(core.DATA)
        with patch.object(queue, "ensure_dispatcher") as dispatcher:
            app.run()
        self.assertFalse(app.exception)
        self.assertTrue(any("数据目录已移动或删除" in error.value for error in app.error))
        self.assertFalse(core.DATA.exists())
        dispatcher.assert_not_called()

    def test_invalid_path_config_shows_a_startup_error(self):
        (core.DATA / "paths.json").write_text("{invalid", encoding="utf-8")
        with patch.object(core, "interviews") as interviews:
            app = AppTest.from_file(str(core.ROOT / "app.py")).run()
        self.assertFalse(app.exception)
        self.assertTrue(any("路径设置" in error.value for error in app.error))
        interviews.assert_not_called()

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
