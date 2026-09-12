from pathlib import Path
import tempfile
import unittest
from echo import library


class LibraryTests(unittest.TestCase):
    def test_company_aliases_and_unknown_year(self):
        items = [dict(id='a', title='08 月 29 日 —— PDD 一面 / media'),
                 dict(id='b', title='09 月 09 日 —— 拼多多 二面 / media'),
                 dict(id='c', title='2025-12-31 —— PDD 一面 / media')]
        group = library.group_items(items, {}, '公司')['拼多多']
        self.assertEqual([i['id'] for i in group], ['c', 'b', 'a'])
        self.assertEqual(library.metadata(items[0], {})['date'], '08-29')
        dates = library.group_items(items, {}, '时间')
        self.assertEqual(list(dates), ['2025 年 12 月', '年份未标注 · 09 月', '年份未标注 · 08 月'])

    def test_metadata_validates_and_preserves_other_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library.update_metadata(root, 'a', date='2026-09-10', company='示例公司')
            library.update_metadata(root, 'b', archived=True)
            library.save_view(root, '公司')
            for invalid in ['09-31', '2026-02-29', '明天']:
                with self.assertRaises(ValueError):
                    library.update_metadata(root, 'a', date=invalid)
            saved = library.read_store(root, 'library.json')
            self.assertEqual(saved['interviews']['a']['date'], '2026-09-10')
            self.assertTrue(saved['interviews']['b']['archived'])
            self.assertEqual(saved['view'], '公司')

    def test_corrupt_store_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'profile.json'
            path.write_text('not json')
            with self.assertRaises(ValueError):
                library.save_profile(root, {'name': 'test'})
            self.assertEqual(path.read_text(), 'not json')
