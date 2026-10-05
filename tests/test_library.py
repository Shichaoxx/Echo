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

    def test_corrupt_store_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'library.json'
            path.write_text('not json')
            with self.assertRaises(ValueError):
                library.update_metadata(root, 'a', company='示例公司')
            self.assertEqual(path.read_text(), 'not json')
