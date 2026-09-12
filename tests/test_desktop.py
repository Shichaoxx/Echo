"""Process ownership checks use synthetic children and do not launch ASR."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from desktop.supervisor import stop_backend
from echo import paths


class DesktopLifecycleTests(unittest.TestCase):
    def test_managed_workers_inherit_backend_group(self):
        with patch.dict(os.environ, {"ECHO_DESKTOP_MANAGED": "1"}):
            self.assertFalse(paths.child_starts_session())
        with patch.dict(os.environ, {"ECHO_DESKTOP_MANAGED": ""}):
            self.assertTrue(paths.child_starts_session())

    def test_stop_reaps_backend_and_its_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            ready = Path(temporary) / "ready"
            code = '''import subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
from pathlib import Path
Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(30)
'''
            process = subprocess.Popen([sys.executable, "-c", code, str(ready)], start_new_session=True)
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists())
                worker = int(ready.read_text())
                self.assertEqual(os.getpgid(worker), process.pid)
                stop_backend(process, grace=0.3)
                self.assertIsNotNone(process.poll())
                # A child may briefly remain a zombie awaiting init's reap; neither
                # a living worker nor a surviving signalable process group is allowed.
                result = subprocess.run(["ps", "-o", "stat=", "-p", str(worker)], capture_output=True, text=True)
                self.assertIn(result.returncode, (0, 1), result.stderr)
                self.assertTrue(not result.stdout.strip() or result.stdout.lstrip().startswith("Z"))
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
