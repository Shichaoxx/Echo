"""Build an Apple Silicon distribution with a verified standalone Python runtime."""
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


PYTHON_ARCHIVE = "cpython-3.13.18+20261124-aarch64-apple-darwin-install_only_stripped.tar.gz"
PYTHON_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20261124/" + PYTHON_ARCHIVE
PYTHON_SHA256 = "296f0903cccda15a5c9d3d8393cd2204718aeffd9b095a00f812d75c7b1c0967"


def bundle_python(resources, dest):
    archive = Path(os.environ.get("ECHO_PYTHON_ARCHIVE", dest / PYTHON_ARCHIVE))
    if not archive.exists():
        subprocess.run(["curl", "--fail", "--location", "--retry", "2",
                        "--output", str(archive), PYTHON_URL], check=True)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != PYTHON_SHA256:
        raise ValueError("Standalone Python archive checksum mismatch")
    with tarfile.open(archive) as package:
        package.extractall(dest, filter="data")
    runtime = resources / "runtime"
    (dest / "python").rename(runtime)
    python = runtime / "bin/python3"
    # Keep wheel entry-point scripts (which include build-time shebangs) out of the app.
    packages = dest / "packages"
    subprocess.run([str(python), "-Im", "pip", "--isolated", "install", "--disable-pip-version-check",
                    "--only-binary=:all:", "--no-compile", "--target", str(packages),
                    "-r", str(ROOT / "requirements-lock.txt")], check=True)
    site_packages = runtime / "lib/python3.13/site-packages"
    for item in packages.iterdir():
        if item.name not in {"bin", "include", "share", "__pycache__"}:
            target = site_packages / item.name
            if target.exists():
                raise ValueError(f"Distribution already contains {item.name}")
            item.rename(target)
    for cache in runtime.rglob("__pycache__"):
        shutil.rmtree(cache)
    (resources / "PYTHON-RUNTIME.txt").write_text(f"{PYTHON_URL}\nSHA256 {PYTHON_SHA256}\n")


def record_media_sources(resources, libraries):
    notices = resources / "media-licenses"
    records = []
    for keg in sorted({path.parents[1] for path in libraries}):
        metadata = json.loads((keg / "sbom.spdx.json").read_text())["packages"][0]
        records.append({key: metadata[key] for key in ("name", "versionInfo", "downloadLocation", "checksums")})
        folder = notices / metadata["name"]
        folder.mkdir(parents=True)
        for path in sorted(keg.iterdir()):
            if path.is_file() and path.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")):
                shutil.copy2(path, folder / path.name)
        for formula in (keg / ".brew").glob("*.rb"):
            shutil.copy2(formula, folder / formula.name)
    (notices / "sources.json").write_text(json.dumps(records, indent=2) + "\n")


def bundle_media_tools(resources):
    """Resolve the Homebrew FFmpeg dependency tree into a private directory."""
    folder = resources / "media-tools"
    folder.mkdir()
    sources = {}
    pending = []

    def include(source, name):
        source = source.resolve()
        if name in sources and sources[name] != source:
            raise ValueError(f"Conflicting media libraries: {name}")
        if name not in sources:
            sources[name] = source
            pending.append(name)

    for name in ("ffmpeg", "ffprobe"):
        include(Path(shutil.which(name)), name)
    while pending:
        name = pending.pop()
        source, target = sources[name], folder / name
        shutil.copy2(source, target)
        target.chmod(0o755)
        changes = []
        for row in subprocess.check_output(["otool", "-L", str(source)], text=True).splitlines()[1:]:
            ref = row.strip().split(" (compatibility version", 1)[0]
            if ref.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if ref.startswith("@loader_path/"):
                dep = source.parent / ref.removeprefix("@loader_path/")
            elif ref.startswith("@rpath/"):
                dep = source.parent / ref.removeprefix("@rpath/")
            else:
                dep = Path(ref)
            if dep.resolve() == source:
                changes += ["-id", "@loader_path/" + name]
            else:
                include(dep, dep.name)
                changes += ["-change", ref, "@loader_path/" + dep.name]
        if changes:
            subprocess.run(["install_name_tool", *changes, str(target)], check=True)
            subprocess.run(["codesign", "--force", "--sign", "-", str(target)], check=True)
    record_media_sources(resources, sources.values())


def write_icon(source_icon, target, dest):
    entries = []
    for size, kind in ((16, b"icp4"), (32, b"icp5"), (64, b"icp6"), (128, b"ic07"),
                       (256, b"ic08"), (512, b"ic09")):
        image = source_icon
        if size < 512:
            image = dest / f"icon-{size}.png"
            subprocess.run(["sips", "-z", str(size), str(size), str(source_icon), "--out", str(image)],
                           stdout=subprocess.DEVNULL, check=True)
        png = image.read_bytes()
        entries.append(struct.pack(">4sI", kind, 8 + len(png)) + png)
    body = b"".join(entries)
    target.write_bytes(struct.pack(">4sI", b"icns", 8 + len(body)) + body)


def build(dest):
    app = dest / "Echo.app"
    binary = app / "Contents/MacOS/Echo"
    binary.parent.mkdir(parents=True)
    subprocess.run(["clang", "-mmacosx-version-min=15.0", "-fobjc-arc", "-framework", "Cocoa", str(ROOT / "macos/main.m"),
                    "-o", str(binary)], check=True)
    resources = app / "Contents/Resources"
    for name in ("web", "echo", "docs"):
        shutil.copytree(ROOT / name, resources / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("AGENTS.md", "VERSION", "LICENSE", "workbench.py", "queue_jobs.py", "macos/desktop.py"):
        shutil.copy2(ROOT / name, resources)
    bundle_python(resources, dest)
    bundle_media_tools(resources)
    plist = app / "Contents/Info.plist"
    info = dict(CFBundleExecutable="Echo", CFBundlePackageType="APPL", CFBundleIconFile="Echo-C2-Dark.icns", CFBundleVersion="7")
    info.update(CFBundleIdentifier="io.github.shichaoxx.echo.app", CFBundleName="Echo",
                LSMinimumSystemVersion="15.0",
                CFBundleShortVersionString=(ROOT / "VERSION").read_text().strip(),
                NSDesktopFolderUsageDescription="Echo 需要读取所选面试资料及保存复盘。")
    plist.write_bytes(plistlib.dumps(info))
    write_icon(ROOT / "docs/figures/echo-icon-dark.png", resources / info["CFBundleIconFile"], dest)
    write_icon(ROOT / "docs/figures/echo-icon.png", resources / "Echo-C2-Light.icns", dest)
    identity = os.environ.get("ECHO_CODESIGN_IDENTITY") or "-"
    subprocess.run(["codesign", "--force", "--sign", identity, str(app)], check=True)
    return app


if __name__ == "__main__":
    if platform.system() != "Darwin" or platform.machine() != "arm64" or sys.version_info < (3, 12):
        raise SystemExit("请在 Apple Silicon Mac 上使用 Python 3.12 或更新版本构建。")
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dist) as tmp:
        app = build(Path(tmp))
        archive = Path(tmp) / "Echo-macOS-arm64.zip"
        subprocess.run(["ditto", "-c", "-k", "--norsrc", "--noextattr", "--noacl", "--keepParent", str(app), str(archive)], check=True)
        archive.replace(dist / archive.name)
        print(dist / archive.name)
