import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from echo import library, paths, workspace_review


class OrganizationTests(unittest.TestCase):
    @patch.dict(os.environ, INTERVIEW_SOURCE_DIR='', INTERVIEW_OUTPUT_DIR='')
    def test_company_output_and_review_agree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'recordings'
            source.mkdir()
            data, output = root / 'data', root / 'reviews'
            paths.save_directories(data, source, output, output_layout='company')
            media = source / '01 月 15 日 —— PDD 一面' / 'recording.mov'
            expected = output / '拼多多' / media.parent.name
            self.assertEqual(paths.mirrored_output(data, media, output), expected)
            self.assertEqual(workspace_review.review_path(media, data), expected / '复盘.md')
            nested = source / '拼多多' / media.parent.name / media.name
            self.assertEqual(paths.mirrored_output(data, nested, output), expected)
            direct = source / '01 月 15 日 —— PDD 一面.mov'
            self.assertEqual(paths.mirrored_output(data, direct, output), output / '拼多多' / direct.stem)
            self.assertEqual(workspace_review.review_path(direct, data), output / '拼多多' / direct.stem / '复盘.md')
            paths.save_directories(data, source, output)
            self.assertEqual(paths._config(data)['output_layout'], 'company')

    def test_project_lifecycle_keeps_records(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory)
            projects = library.manage_project(data, {'operation':'create', 'name':'Agent 岗位'})
            key = next(iter(projects))
            library.update_metadata(data, 'sample', project_id=key, company='示例公司')
            library.manage_project(data, {'operation':'rename', 'id':key, 'name':'秋招'})
            self.assertEqual(library.read_store(data, 'library.json')['projects'][key]['name'], '秋招')
            with self.assertRaises(ValueError):
                library.manage_project(data, {'operation':'create', 'name':'秋招'})
            with self.assertRaises(ValueError):
                library.update_metadata(data, 'sample', project_id='missing')
            library.manage_project(data, {'operation':'delete', 'id':key})
            store = library.read_store(data, 'library.json')
            self.assertEqual(store['projects'], {})
            self.assertEqual(store['interviews']['sample']['project_id'], '')
            self.assertEqual(store['interviews']['sample']['company'], '示例公司')
