"""Archive invariants on synthetic private data only."""
import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('archive', Path(__file__).resolve().parents[1] / 'scripts/archive_interviews.py')
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name).resolve()
        self.source, self.data, self.dest = root / 'source', root / 'data', root / 'archive'
        self.source.mkdir()
        self.data.mkdir()
        self.video = self.source / 'session.mov'
        self.video.write_bytes(b'synthetic')
        self.output = self.data / 'outputs' / 'run'
        self.output.mkdir(parents=True)
        (self.output / 'transcript.json').write_text('{}')
        with closing(sqlite3.connect(self.data / 'ledger.sqlite3')) as c, c:
            c.executescript('CREATE TABLE interviews(id,path,sha256); CREATE TABLE runs(id,interview_id,state,started_at,output_dir); CREATE TABLE notes(seq,interview_id,at,body);')
            c.execute('INSERT INTO interviews VALUES(?,?,?)', ('one', str(self.video), 'hash'))
            c.execute('INSERT INTO runs VALUES(?,?,?,?,?)', ('run', 'one', 'completed', 'now', str(self.output)))
            c.execute('INSERT INTO notes VALUES(?,?,?,?)', (1, 'one', 'now', 'original note'))

    def build(self):
        return archive.build(self.source, self.data, self.dest)

    def test_repeat_preserves_notes_versions_and_snapshots(self):
        first = self.build()
        folder = self.dest / first['sessions'][0]['folder']
        review = folder / '03_复盘/复盘.md'
        review.write_text('my review')
        second = self.build()
        self.assertEqual(review.read_text(), 'my review')
        self.assertEqual(self.video.read_bytes(), b'synthetic')
        self.assertEqual(len(list((self.dest / '90_归档记录').iterdir())), 2)
        self.assertNotEqual(first['generation'], second['generation'])
        self.assertTrue((folder / '02_转写版本/run/transcript.json').is_file())
        with closing(sqlite3.connect(self.dest / '90_归档记录' / first['generation'] / 'ledger.sqlite3')) as c:
            self.assertEqual(c.execute('SELECT body FROM notes').fetchone()[0], 'original note')

    def test_missing_source_and_result_are_visible(self):
        self.build()
        self.video.unlink()
        (self.output / 'transcript.json').unlink()
        status = self.build()['sessions'][0]['status']
        self.assertIn('源文件缺失', status)
        self.assertIn('结果文件缺失', status)

    def test_new_recording_keeps_folder_after_registration(self):
        new = self.source / 'new.mov'
        new.write_bytes(b'new')
        first = next(r for r in self.build()['sessions'] if r['source'] == str(new))
        self.assertEqual(first['status'], '待 Echo 扫描')
        with closing(sqlite3.connect(self.data / 'ledger.sqlite3')) as c, c:
            c.execute('INSERT INTO interviews VALUES(?,?,?)', ('two', str(new), 'newhash'))
        second = next(r for r in self.build()['sessions'] if r['source'] == str(new))
        self.assertEqual(first['folder'], second['folder'])
        self.assertEqual(second['status'], '待转写')

    def test_no_archive_under_source_or_overwrite_conflicting_file(self):
        with self.assertRaises(ValueError):
            archive.build(self.source, self.data, self.source / 'archive')
        first = self.build()
        link = self.dest / first['sessions'][0]['folder'] / '01_原始资料/session.mov'
        link.unlink()
        link.write_text('keep')
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual(link.read_text(), 'keep')
