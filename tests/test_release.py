import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("public_check", Path(__file__).resolve().parents[1] / "scripts" / "check_public.py")
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)


class ReleaseChecks(unittest.TestCase):
    def test_private_files_rejected(self):
        self.assertTrue(public.scan(".local/ledger.sqlite3", b""))
        self.assertTrue(public.scan("recordings/sample.wav", b""))
        self.assertTrue(public.scan("transcript.json", b"{}"))

    def test_machine_path_and_tokens_rejected_without_echo(self):
        self.assertTrue(public.scan("notes.md", ("/" + "Users" + "/example/private").encode()))
        self.assertTrue(public.scan("config.py", ("sk-" + "x" * 30).encode()))

    def test_generic_document_is_allowed(self):
        self.assertEqual(public.scan("README.md", b"Use recordings/ and local inference."), [])
