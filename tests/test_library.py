"""Synthetic company aliases and dates; no interview records are used."""
from pathlib import Path
import tempfile
import unittest
from echo import library


class LibraryTests(unittest.TestCase):
    def test_company_aliases_and_unknown_year(self):
        items = [dict(id='a', title='01 月 15 日 —— PDD 一面 / synthetic-media'),
                 dict(id='b', title='01 月 20 日 —— 拼多多 二面 / synthetic-media'),
                 dict(id='c', title='2025-12-31 —— PDD 一面 / media')]
        self.assertEqual([library.metadata(item, {})['company'] for item in items], ['拼多多'] * 3)
        self.assertEqual(library.metadata(items[0], {})['date'], '01-15')
        self.assertEqual(library.metadata(items[2], {})['date'], '2025-12-31')

    def test_metadata_validates_and_preserves_other_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library.update_metadata(root, 'a', date='2026-09-10', company='示例公司')
            library.update_metadata(root, 'b', archived=True)
            for invalid in ['09-31', '2026-02-29', '明天']:
                with self.assertRaises(ValueError):
                    library.update_metadata(root, 'a', date=invalid)
            saved = library.read_store(root, 'library.json')
            self.assertEqual(saved['interviews']['a']['date'], '2026-09-10')
            self.assertTrue(saved['interviews']['b']['archived'])

    def test_dated_folder_groups_unlisted_company_and_preserves_manual_override(self):
        for company, stage in [('星图研发中心', '一面'), ('42 云舟', '二面'), ('Example Labs', 'HR 面')]:
            with self.subTest(company=company):
                item = {'id': 'sample', 'title': f'2026 年 07 月 08 日 —— {company} {stage} / recording'}
                result = library.metadata(item, {})
                self.assertEqual((result['company'], result['round'], result['date']),
                                 (company, stage, '2026-07-08'))
                stored = {'interviews': {'sample': {'company': '手动分组'}}}
                self.assertEqual(library.metadata(item, stored)['company'], '手动分组')

    def test_missing_or_invalid_company_stays_unclassified(self):
        for company in ['', '.', '..', '嵌套/目录', '额外\\目录']:
            with self.subTest(company=company):
                title = f'2026-07-08 —— {company} 二面'
                self.assertEqual(library.inferred_metadata({'title': title})['company'], '未分类')

    def test_corrupt_store_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'library.json'
            path.write_text('not json')
            with self.assertRaises(ValueError):
                library.update_metadata(root, 'a', company='示例公司')
            self.assertEqual(path.read_text(), 'not json')
