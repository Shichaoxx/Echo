import importlib.util
import contextlib
import io
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch


spec = importlib.util.spec_from_file_location("public_check", Path(__file__).resolve().parents[1] / "scripts" / "check_public.py")
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)


class PublicFileChecks(unittest.TestCase):
    def test_working_tree_checks_new_files_and_skips_removed_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'README.md').write_text('Echo workbench')
            (root / 'new.py').write_text('/' + 'Users' + '/example/private')
            def git(*args):
                if args == ('ls-files', '--deleted', '-z'):
                    return b'removed.py\0'
                self.assertEqual(args, ('ls-files', '--cached', '--others', '--exclude-standard', '-z'))
                return b'README.md\0new.py\0removed.py\0'
            with patch.object(public, 'ROOT', root), patch.object(public, 'git', side_effect=git):
                self.assertEqual(public.check_tree(), [('new.py', 'personal-machine-path')])

    def test_private_files_rejected(self):
        self.assertTrue(public.scan(".local/ledger.sqlite3", b""))
        self.assertTrue(public.scan("recordings/sample.wav", b""))
        self.assertTrue(public.scan("transcript.json", b"{}"))

    def test_memory_runtime_and_review_outputs_are_private(self):
        paths = [
            'memory/MEMORY.md', 'Memory/profile.md', 'memory/topics/skill.md',
            'results/memory/topics/skill.md', 'runtime/events.jsonl',
            'runtime/task-summary.md', '.runtime/state.json',
            'knowledge/.echo-memory.json', 'memory-locations.json',
            'memory-locations.lock', 'MEMORY.md', 'profile.md', 'review.md',
            'interviews/复盘.md', 'interviews/synthetic.复盘.md',
            'interviews/synthetic.review.md', 'PROJECT_CONTEXT.md',
        ]
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue(public.scan(path, b'Synthetic local data'))

    def test_public_protocol_and_templates_still_scan_their_content(self):
        allowed = ['docs/memory.md', 'docs/codex-review.md', 'docs/usage.md',
                   'docs/templates/review.md', 'docs/templates/profile.md',
                   'docs/templates/MEMORY.md', 'docs/templates/复盘.md',
                   'docs/templates/memory/MEMORY.md', 'docs/templates/memory/topics/example.md',
                   'echo/memory.py', 'tests/test_memory.py']
        for path in allowed:
            with self.subTest(path=path):
                self.assertEqual(public.scan(path, b'Generic protocol or template'), [])
                self.assertIn('personal-machine-path', public.scan(path, ('/' + 'Users' + '/example/private').encode()))
        self.assertTrue(public.scan('docs/templates/runtime/details.md', b'Local state'))
        self.assertTrue(public.scan('docs/templates/memory/runtime/details.md', b'Local state'))
        self.assertTrue(public.scan('docs/templates/review.json', b'{}'))

    def test_ignored_memory_marker_protects_untracked_sibling_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'knowledge/topics').mkdir(parents=True)
            (root / 'knowledge/.echo-memory.json').write_text('{}')
            (root / 'knowledge/topics/skill.md').write_text('Synthetic private topic')
            def git(*args):
                return b'' if args == ('ls-files', '--deleted', '-z') else b'knowledge/topics/skill.md\0'
            with patch.object(public, 'ROOT', root), patch.object(public, 'git', side_effect=git):
                self.assertEqual(public.check_tree(), [('knowledge/topics/skill.md', 'private-memory-root')])

    def test_historical_memory_marker_protects_arbitrary_root_names(self):
        def git(*args):
            if args == ('ls-tree', '-r', '--name-only', '-z', 'revision'):
                return b'knowledge/.echo-memory.json\0knowledge/topics/skill.md\0'
            self.assertEqual(args[0], 'show')
            return b'Synthetic private topic'
        with patch.object(public, 'git', side_effect=git):
            findings = public.check_tree('revision')
        self.assertIn(('knowledge/topics/skill.md', 'private-memory-root'), findings)

    def test_machine_path_and_tokens_rejected_without_echo(self):
        self.assertTrue(public.scan("notes.md", ("/" + "Users" + "/example/private").encode()))
        self.assertTrue(public.scan("config.py", ("sk-" + "x" * 30).encode()))

    def test_machine_paths_cover_unicode_names_and_windows_paths(self):
        samples = ['/' + 'Users' + '/示例/private', '/' + 'home' + '/sample/private',
                   'C:' + '\\Users\\sample\\private', 'C:' + '\\\\Users\\\\sample\\\\private']
        for value in samples:
            with self.subTest(style='windows' if ':' in value else 'unix'):
                self.assertIn('personal-machine-path', public.scan('notes.md', value.encode()))

    def test_history_checks_messages_and_tag_email_without_printing_content(self):
        commit, tag = 'a' * 40, 'b' * 40
        private_email = 'contributor' + '@' + 'example.net'
        private_path = '/' + 'Users' + '/example/private'
        def git(*args):
            responses = {
                ('rev-list', '--all'): commit,
                ('show', '-s', '--format=%ae%n%ce', commit): '123+example' + '@' + 'users.noreply.github.com',
                ('show', '-s', '--format=%B', commit): private_path,
                ('for-each-ref', '--format=%(objecttype) %(objectname)', 'refs/tags'): 'tag ' + tag,
                ('cat-file', 'tag', tag): 'object ' + commit + '\ntagger Example <' + private_email + '> 0 +0000\n\nGeneric release note',
            }
            return responses[args].encode()
        with patch.object(public, 'git', side_effect=git), patch.object(public, 'check_tree', return_value=[]):
            findings = public.check_history()
            self.assertIn((commit[:12] + ':commit-message', 'personal-machine-path'), findings)
            self.assertIn((tag[:12] + ':annotated-tag', 'tag-email-review-required'), findings)
            self.assertNotIn((commit[:12], 'commit-email-review-required'), findings)
            output = io.StringIO()
            with patch('sys.argv', ['check_public.py', '--history']), contextlib.redirect_stdout(output):
                self.assertTrue(public.main())
            self.assertNotIn(private_email, output.getvalue())
            self.assertNotIn(private_path, output.getvalue())

    def test_history_allows_github_noreply_tag_identity_but_checks_its_message(self):
        tag = 'b' * 40
        message = 'contact: ' + 'example' + '@' + 'example.net'
        def git(*args):
            if args == ('rev-list', '--all'):
                return b''
            if args == ('for-each-ref', '--format=%(objecttype) %(objectname)', 'refs/tags'):
                return ('commit ' + 'a' * 40 + '\ntag ' + tag).encode()
            return ('tagger Example <123+example' + '@' + 'users.noreply.github.com> 0 +0000\n\n' + message).encode()
        with patch.object(public, 'git', side_effect=git):
            self.assertEqual(public.check_history(), [(tag[:12] + ':tag-message', 'email-review-required')])

    def test_generic_document_is_allowed(self):
        self.assertEqual(public.scan("README.md", b"Use recordings/ and local inference."), [])

    def test_reviewed_exports_require_exact_path_and_content(self):
        for path in public.REVIEWED_ARTIFACTS:
            with self.subTest(path=path):
                content = (public.ROOT / path).read_bytes()
                self.assertEqual(public.scan(path, content), [])
                self.assertIn("binary-review-required", public.scan(path, content + b"changed"))
                self.assertIn("binary-review-required", public.scan("other.png", content))
                self.assertIn("private-or-generated-directory", public.scan(".local/other.png", content))
