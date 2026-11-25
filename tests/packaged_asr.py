"""Smoke-test real offline speech recognition in a relocated macOS distribution.

    python3 tests/packaged_asr.py "/Volumes/Echo 1/Echo 1.app"
"""

import argparse
import hashlib
import json
import subprocess
import tempfile
import time
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.request import HTTPCookieProcessor, ProxyHandler, Request, build_opener


def check(app):
    resources = app.resolve() / "Contents/Resources"
    python = resources / "runtime/bin/python3"
    with tempfile.TemporaryDirectory(prefix="echo-release-asr-") as folder:
        home = Path(folder)
        environment = {
            "HOME": folder,
            "PATH": str(resources / "media-tools") + ":/usr/bin:/bin",
            "TMPDIR": folder,
            "HF_HUB_OFFLINE": "1",
            "LC_CTYPE": "UTF-8",
        }
        proc = subprocess.Popen(
            [str(python), "-BEs", "-m", "server", "--port", "0", "--exit-on-stdin-eof",
             "--data-dir", str(home / "library"), "--model-dir", str(resources / "model-turbo")],
            cwd=resources, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True,
        )
        try:
            base = json.loads(proc.stdout.readline())["url"].rstrip("/")
            local = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
            headers = {}

            def call(path, data=None, extra=None):
                req = Request(base + path, data=data, headers={**headers, **(extra or {})})
                with local.open(req, timeout=20) as reply:
                    return json.load(reply)

            headers["X-Echo-CSRF"] = call("/api/bootstrap")["csrf"]
            audio = (resources / "examples/reading-room/audio.mp3").read_bytes()
            key = call(
                "/api/recordings?title=ReleaseSmokeTest", audio,
                {"Content-Type": "application/octet-stream", "X-Echo-Filename": "sample.mp3"},
            )["recording"]["id"]
            path = "/api/recordings/" + key
            job = call(path + "/transcribe", b"{}", {"Content-Type": "application/json"})["job"]
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                status = call(path)["job"]
                if status["id"] != job["id"]:
                    raise RuntimeError("Unexpected ASR job")
                if status["state"] == "completed":
                    break
                if status["state"] == "failed":
                    raise RuntimeError(status["error"])
                time.sleep(0.5)
            else:
                raise TimeoutError("Packaged ASR timed out")
            source = call(path + "/runs/" + status["run_id"])
            transcript = "".join(s["text"] for s in source["segments"])
            if "预约" not in transcript or len(source["segments"]) < 2:
                raise RuntimeError("Audio was not transcribed as expected")
            if source["source_sha256"] != hashlib.sha256(audio).hexdigest() or not source["media_url"]:
                raise RuntimeError("Transcript and packaged media did not match")
            print(f"{source['run']['model']}: {len(source['segments'])} segments, "
                  f"{source['run']['scope']['end']:.2f}s, verified original audio, offline")
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=5)
            proc.stdout.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    check(parser.parse_args().app)
