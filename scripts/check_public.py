"""Conservative release hygiene checks; findings never print source excerpts."""
import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BLOCKED_SUFFIXES = {".mov", ".mp4", ".mkv", ".wav", ".mp3", ".m4a", ".webm",
                    ".ogg", ".flac", ".srt", ".vtt", ".sqlite", ".sqlite3",
                    ".db", ".log", ".safetensors", ".npz", ".pem", ".key"}
PRIVATE_DIRECTORIES = {".local", ".venv", ".git", ".cache", "recordings", "dist",
                       "__pycache__", "archives", "memory", "memories", "runtime",
                       ".runtime", ".state", "10_逐场面试", "20_跨场总结", "90_归档记录"}
PRIVATE_FILENAMES = {
    "transcript.txt", "transcript.json", "transcript.md", "review.json",
    "review.md", "复盘.md", "memory.md", "profile.md", "preferences.json",
    "profile.json", "library.json", "source_path.txt", "archive_path.txt",
    "paths.json", "manifest.json", "interview.json", "navigation.json", "migration.json", "档案.json", ".echo-memory.json",
    "memory-locations.json", "memory-locations.lock",
}
CONTEXT_FILENAMES = {"project_context.md", "personal_context.md"}
MEMORY_MARKER = ".echo-memory.json"
# Exact exports of public design SVGs, visually reviewed for layout and
# content. Regenerated or replaced binaries require a fresh review and hash.
REVIEWED_ARTIFACTS = {
    "docs/figures/echo-architecture.pdf": "5817632fee2934fe6cbb868be8374927d562e9cb699187adfbfeaeb588a5ce53",
    "docs/figures/echo-architecture.png": "ace7fd4372e383d54d16410601d8054909fb0b97daeedbf7aaf78083a2c81e07",
    "docs/figures/echo-icon.png": "5fe5ab81b55a747283e54940a9c283b6ad29a8092ce6ef3e49cafb744ff5fc46",
}
EMAIL_PATTERN = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PATTERNS = [
    ("personal-machine-path", re.compile(r"/(?:Users|home)/[^/\s\"'<>]+/|[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'<>]+[\\/]")),
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("credential-like-token", re.compile(r"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,})")),
    ("email-review-required", EMAIL_PATTERN),
]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def public_markdown(path):
    """Allow public protocol docs and Markdown templates, not arbitrary outputs.

    These locations waive only reserved output basenames. Directory, marker,
    binary and content checks still apply to every file in those locations.
    """
    return path.as_posix() == "docs/memory.md" or (
        path.parts[:2] == ("docs", "templates") and path.suffix.lower() == ".md"
    )


def scan(path, content, private_roots=()):
    p = PurePosixPath(path)
    reasons = []
    directory_parts = p.parts[:-1]
    if p.parts[:3] == ("docs", "templates", "memory") and p.suffix.lower() == ".md":
        # A documented blank memory template is public. This exact prefix does
        # not exempt a nested runtime directory or a real memory-root marker.
        directory_parts = p.parts[3:-1]
    if any(part.casefold() in PRIVATE_DIRECTORIES for part in directory_parts):
        reasons.append("private-or-generated-directory")
    if any(p == root or root in p.parents for root in private_roots):
        reasons.append("private-memory-root")
    if p.suffix.lower() in BLOCKED_SUFFIXES or (p.name.startswith(".env") and p.name != ".env.example"):
        reasons.append("private-or-binary-file-type")
    name = p.name.casefold()
    is_output = name in PRIVATE_FILENAMES or name.endswith((".复盘.md", ".review.md"))
    if is_output and not public_markdown(p):
        reasons.append("personal-output")
    if name in CONTEXT_FILENAMES:
        reasons.append("context-document-review-required")
    try:
        value = content.decode("utf-8")
    except UnicodeDecodeError:
        if not reasons and REVIEWED_ARTIFACTS.get(p.as_posix()) == hashlib.sha256(content).hexdigest():
            return []
        return reasons + ["binary-review-required"]
    for label, pattern in PATTERNS:
        if pattern.search(value):
            reasons.append(label)
    return reasons


def check_tree(revision=None):
    if revision:
        paths = git("ls-tree", "-r", "--name-only", "-z", revision).decode().split("\0")
    else:
        # Check the actual proposed source tree, including new files and excluding
        # deliberate working-tree deletions. Ignored private data stays excluded.
        deleted = set(git("ls-files", "--deleted", "-z").decode().split("\0"))
        paths = set(git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")) - deleted
    paths = sorted(filter(None, paths))
    private_roots = {PurePosixPath(path).parent for path in paths if PurePosixPath(path).name == MEMORY_MARKER}
    if not revision:
        # A local marker may itself be ignored. Its selected memory directory
        # must still protect a newly staged sibling or an untracked topic note.
        for path in paths:
            for parent in PurePosixPath(path).parents:
                if (ROOT / parent / MEMORY_MARKER).is_file():
                    private_roots.add(parent)
    findings = []
    for path in paths:
        if revision:
            data = git("show", f"{revision}:{path}")
        else:
            target = ROOT / path
            if not target.is_file() or target.is_symlink():
                findings.append((path, "missing-or-symlink"))
                continue
            data = target.read_bytes()
        findings.extend((path, reason) for reason in scan(path, data, private_roots))
    return findings


def check_history():
    """Inspect reachable commit trees and messages plus annotated tag metadata.

    Git identities remain part of history: a passing result is not an anonymity
    guarantee. Findings report only object/path locations and categories.
    """
    findings = []
    for commit in git("rev-list", "--all").decode().splitlines():
        location = commit[:12]
        findings.extend((f"{location}:{path}", reason) for path, reason in check_tree(commit))
        for email in git("show", "-s", "--format=%ae%n%ce", commit).decode().splitlines():
            if email and not email.endswith("@users.noreply.github.com"):
                findings.append((location, "commit-email-review-required"))
        message = git("show", "-s", "--format=%B", commit)
        findings.extend((f"{location}:commit-message", reason) for reason in scan("commit-message", message))
    tags = git("for-each-ref", "--format=%(objecttype) %(objectname)", "refs/tags").decode().splitlines()
    for tag in tags:
        kind, object_id = tag.split(" ", 1)
        if kind != "tag":
            continue
        headers, _, message = git("cat-file", "tag", object_id).partition(b"\n\n")
        for line in headers.decode(errors="replace").splitlines():
            if line.startswith("tagger "):
                for email in EMAIL_PATTERN.findall(line):
                    if not email.endswith("@users.noreply.github.com"):
                        findings.append((f"{object_id[:12]}:annotated-tag", "tag-email-review-required"))
        findings.extend((f"{object_id[:12]}:tag-message", reason) for reason in scan("tag-message", message))
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true")
    args = parser.parse_args()
    findings = check_tree()
    if args.history:
        findings.extend(check_history())
    for path, reason in sorted(set(findings)):
        print(f"{path}: {reason}")
    print(f"Public-file check: {'FAIL' if findings else 'PASS'}")
    return bool(findings)


if __name__ == "__main__":
    sys.exit(main())
