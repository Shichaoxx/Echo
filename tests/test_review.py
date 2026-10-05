import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
from echo import workspace_review as review
from echo import paths
from echo.transcription_ui import range_error, scope_label


class ReviewTests(unittest.TestCase):
    def test_template_and_legacy_and_mismatch(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / '复盘.md'
            path.write_text(review.template({'id': 'example'}, []))
            doc = review.read_document(path, kind='interview', interview_id='example')
            self.assertEqual(doc['warnings'], [])
            self.assertEqual(doc['metadata']['source_run_ids'], [])
            self.assertTrue(review.read_document(path, kind='interview', interview_id='other')['warnings'])
            path.write_text('# Old notes\nkeep')
            doc = review.read_document(path, kind='interview')
            self.assertIn('keep', doc['body'])
            self.assertTrue(doc['warnings'])
            path.write_text('<!-- echo-review\n[]\n-->\n# keep')
            self.assertTrue(review.read_document(path, kind='interview')['warnings'])

    def test_multiple_recordings_cannot_share_review(self):
        with tempfile.TemporaryDirectory() as root:
            first = Path(root) / 'first.mov'
            first.touch()
            self.assertEqual(review.review_path(first).name, '复盘.md')
            (Path(root) / 'second.mov').touch()
            self.assertEqual(review.review_path(first).name, 'first.复盘.md')

    def test_new_reviews_follow_output_and_existing_source_reviews_stay_in_place(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"INTERVIEW_SOURCE_DIR": "", "INTERVIEW_OUTPUT_DIR": ""}):
            data, source = Path(root).resolve() / 'data', Path(root).resolve() / 'recordings'
            source.mkdir()
            media = source / 'first.mov'
            media.touch()
            paths.save_directories(data, source, source / 'echo', default_output=True)
            target = source / 'echo' / '复盘.md'
            self.assertEqual(review.review_path(media, data), target)
            self.assertFalse(target.parent.exists())
            legacy = source / '复盘.md'
            legacy.write_text('Preserved old review')
            self.assertEqual(review.review_path(media, data), legacy)
            target.parent.mkdir()
            target.write_text('Current output review')
            self.assertEqual(review.review_path(media, data), target)
            self.assertEqual(legacy.read_text(), 'Preserved old review')
            (source / 'second.mov').touch()
            self.assertEqual(review.review_path(media, data), target.parent / 'first.复盘.md')

    def test_range_validation_and_end_scope(self):
        self.assertIsNone(range_error(0, 0, 100))
        self.assertTrue(range_error(100, 0, 100))
        self.assertTrue(range_error(90, 20, 100))
        self.assertIn('至录屏结束', scope_label({'start': 30, 'limit': 0}))

    def test_latest_action_status_without_rewriting_history(self):
        body = '| 行动 ID | 状态 |\n| --- | --- |\n| A01 | 待回听 |\n\n## 追加\n| 行动 ID | 状态 |\n| --- | --- |\n| A01 | 已验证 |\n| A02 | 待做 |'
        self.assertEqual(review.action_items(body), [{'行动 ID': 'A01', '状态': '已验证'}, {'行动 ID': 'A02', '状态': '待练习'}])
