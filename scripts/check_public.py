"""Conservative release hygiene checks; findings never print source excerpts."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_SUFFIXES = {".mov", ".mp4", ".mkv", ".wav", ".mp3", ".m4a", ".webm",
                    ".ogg", ".flac", ".srt", ".vtt", ".sqlite", ".sqlite3",
                    ".db", ".log", ".safetensors", ".npz", ".pem", ".key"}
PATTERNS = [
    ("personal-machine-path", re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("credential-like-token", re.compile(r"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,})")),
    ("email-review-required", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def scan(path, content):
    p = Path(path)
    reasons = []
    if any(part in {".local", ".venv", ".git", "recordings", "dist", "__pycache__"} for part in p.parts):
        reasons.append("private-or-generated-directory")
    if p.suffix.lower() in BLOCKED_SUFFIXES or (p.name.startswith(".env") and p.name != ".env.example"):
        reasons.append("private-or-binary-file-type")
    if p.name in {"transcript.txt", "transcript.json", "review.json", "preferences.json", "source_path.txt"}:
        reasons.append("personal-output")
    try:
        value = content.decode("utf-8")
    except UnicodeDecodeError:
        return reasons + ["binary-review-required"]
    for label, pattern in PATTERNS:
        if pattern.search(value):
            reasons.append(label)
    return reasons


def check_tree(revision=None):
    paths = git("ls-tree", "-r", "--name-only", "-z", revision).decode().split("\0") if revision else git("ls-files", "-z").decode().split("\0")
    findings = []
    for path in filter(None, paths):
        if revision:
            data = git("show", f"{revision}:{path}")
        else:
            target = ROOT / path
            if not target.is_file() or target.is_symlink():
                findings.append((path, "missing-or-symlink"))
                continue
            data = target.read_bytes()
        findings.extend((path, reason) for reason in scan(path, data))
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    findings = check_tree()
    if args.history:
        for commit in git("rev-list", "--all").decode().splitlines():
            findings.extend((f"{commit[:12]}:{path}", reason) for path, reason in check_tree(commit))
            for email in git("show", "-s", "--format=%ae%n%ce", commit).decode().splitlines():
                if email and not email.endswith("@users.noreply.github.com"):
                    findings.append((commit[:12], "commit-email-review-required"))
            # Context notes require manual review even if no pattern matches.
            paths = git("ls-tree", "-r", "--name-only", commit).decode().splitlines()
            if "PROJECT_CONTEXT.md" in paths:
                findings.append((commit[:12] + ":PROJECT_CONTEXT.md", "historical-context-review-required"))
    for path, reason in sorted(set(findings)):
        print(f"{path}: {reason}")
    print(f"Public-file check: {'FAIL' if findings else 'PASS'}")
    return bool(findings)


if __name__ == "__main__":
    sys.exit(main())
