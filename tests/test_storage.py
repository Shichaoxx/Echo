import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from echo import interview as core, paths, storage, library, migrate_library
from echo.web_service import WebService, APIError


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.source = self.base / 'recordings'
        self.source.mkdir()
        self.data = self.base / 'data'
        self.env = patch.dict(os.environ, {'INTERVIEW_SOURCE_DIR': '', 'INTERVIEW_OUTPUT_DIR': ''})
        self.env.start(); self.addCleanup(self.env.stop)
        self.old_data = core.DATA; self.addCleanup(setattr, core, 'DATA', self.old_data)
        core.DATA = self.data
        paths.save_directories(self.data, self.source, self.source / 'echo', default_output=True)
        self.media = self.source / 'CompanyA' / '2026-09-01_first' / 'recording.wav'
        self.media.parent.mkdir(parents=True)
        self.media.write_bytes(b'synthetic original media')
        core.scan(settle_seconds=0)
        self.key = core.interviews()[0]['id']

    def fixture(self):
        folder = self.source / 'echo' / 'CompanyA' / 'first'
        folder.mkdir(parents=True)
        value = {'run_id': 'run-one', 'source_sha256': core.digest(self.media), 'model': 'test',
                 'created_at': '2026-09-01T10:00:00', 'options': {'start': 0, 'limit': 0},
                 'result': {'text': 'synthetic', 'segments': []}}
        (folder / 'transcript.json').write_text(json.dumps(value))
        (folder / 'transcript.txt').write_text('synthetic')
        (folder / 'review.md').write_text('automatic check draft')
        (folder / '复盘.md').write_text('# Synthetic review\n[notes](notes.md)')
        (folder / 'notes.md').write_text('supplemental')
        core.add_note(self.key, 'retain this note')
        return folder

    def test_migration_preserves_identity_notes_and_every_transcript(self):
        old = self.fixture()
        value = migrate_library.plan(self.source, self.data)
        self.assertTrue(old.exists())
        self.assertFalse((self.source / 'echo/.state').exists())
        report = migrate_library.apply(value)
        self.assertEqual(report['runs'], 1)
        data = self.source / 'echo/.state'
        self.assertEqual(core.interviews()[0]['id'], self.key)
        with core.db() as c:
            self.assertEqual(c.execute('SELECT body FROM notes').fetchone()[0], 'retain this note')
        folder, info = storage.session(data, self.key)
        self.assertEqual(info['preferred_run_id'], 'run-one')
        self.assertTrue((folder / 'transcripts/run-one/transcript.json').is_file())
        self.assertTrue((Path(report['backup']) / 'echo/CompanyA/first/transcript.json').is_file())
        self.assertEqual(storage.review(data, self.key), folder / '复盘.md')
        self.assertEqual(storage.documents(data, self.key)[0]['title'], 'notes')
        with self.assertRaises(ValueError):
            migrate_library.plan(self.source, self.data)

    def test_rename_keeps_id_runs_and_notes(self):
        core.add_note(self.key, 'note')
        moved = self.media.with_name('renamed.wav')
        self.media.rename(moved)
        core.scan(settle_seconds=0)
        self.assertEqual(core.interviews()[0]['id'], self.key)
        self.assertEqual(core.interviews()[0]['path'], str(moved))
        self.assertEqual(len(core.interviews()), 1)

    def test_ambiguous_duplicate_does_not_get_silently_relinked(self):
        self.fixture()
        (self.media.parent / 'duplicate.wav').write_bytes(self.media.read_bytes())
        with self.assertRaises(ValueError):
            migrate_library.plan(self.source, self.data)
        self.assertFalse((self.source / 'echo/.state').exists())

    def test_preferred_run_survives_newer_completion_and_is_used_for_handoff(self):
        self.fixture(); migrate_library.apply(migrate_library.plan(self.source, self.data))
        data = self.source / 'echo/.state'
        service = WebService(data)
        with core.db() as c:
            first = dict(c.execute('SELECT * FROM runs').fetchone())
            c.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?)', ('run-two', self.key, first['source_sha256'], 'test', '{}', 'completed', '2026-10-01', '2026-10-01', first['output_dir'], None))
        with patch.object(service, 'duration', return_value=3):
            self.assertEqual(service.details(self.key)['run']['id'], 'run-one')
            self.assertEqual(service.details(self.key, 'run-two')['run']['id'], 'run-two')
        storage.select_run(data, self.key, 'run-two')
        self.assertEqual(storage.selected_run(data, self.key), 'run-two')

    def test_traversal_in_manifest_is_rejected(self):
        with self.assertRaises(ValueError):
            storage.contained(self.source, '../outside.md')
        with self.assertRaises(ValueError):
            storage.contained(self.source, '/etc/passwd')

    def test_new_managed_record_gets_own_version_directory(self):
        self.fixture(); migrate_library.apply(migrate_library.plan(self.source, self.data))
        media = self.source / 'new.wav'; media.write_bytes(b'new source')
        core.scan(settle_seconds=0)
        key = next(x['id'] for x in core.interviews() if x['path'] == str(media))
        result = storage.transcription_directory(core.DATA, key, 'new-run')
        self.assertEqual(result.name, 'new-run')
        self.assertEqual(result.parent.name, 'transcripts')

    def test_migration_failure_restores_original_output(self):
        folder = self.fixture()
        before = migrate_library.fingerprint(self.source / 'echo')
        value = migrate_library.plan(self.source, self.data)
        original = library.write_store
        def fail_on_session(data, name, body):
            if name == 'interview.json':
                raise OSError('synthetic interrupted write')
            return original(data, name, body)
        with patch.object(library, 'write_store', side_effect=fail_on_session):
            with self.assertRaises(OSError):
                migrate_library.apply(value)
        self.assertEqual(migrate_library.fingerprint(self.source / 'echo'), before)

    def test_relink_moved_library_keeps_notes_and_run_identity(self):
        self.fixture(); migrate_library.apply(migrate_library.plan(self.source, self.data))
        moved = self.base / 'renamed-library'
        self.source.rename(moved)
        data = moved / 'echo/.state'
        result = storage.relink(data)
        self.assertEqual(result['relinked'], 1)
        core.DATA = data
        self.assertTrue(Path(core.interviews()[0]['path']).is_file())
        self.assertTrue(Path(core.runs(self.key)[0]['output_dir'], 'transcript.json').is_file())
        with core.db() as c:
            self.assertEqual(c.execute('SELECT body FROM notes').fetchone()[0], 'retain this note')

    def test_relinked_review_keeps_prior_id_as_alias(self):
        folder = self.fixture()
        (folder / '复盘.md').write_text('<!-- echo-review\n' + json.dumps({'interview_id': 'newer-id', 'schema_version': 1, 'kind': 'interview', 'source_run_ids': ['run-one']}) + '\n-->\n# Evidence')
        migrate_library.apply(migrate_library.plan(self.source, self.data))
        folder, value = storage.session(core.DATA, self.key)
        self.assertEqual(value['legacy_ids'], ['newer-id'])
        doc = (folder / '复盘.md').read_text()
        self.assertIn(self.key, doc)
        self.assertIn('newer-id', doc)
