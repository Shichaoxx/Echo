"""Build Echo for Apple Silicon with a self-contained offline transcription runtime."""

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECIPES = ROOT / "scripts/macos"
VERSION = json.loads((ROOT / "frontend/package.json").read_text())["version"]
BUILD_NUMBER = "2"  # Increase for each release, including prereleases.
APP_NAME = "Echo"
STEM = f"Echo-{VERSION}-macOS-arm64"
MACOS_MIN = "15.0"
PYTHON_ARCHIVE = "cpython-3.13.18+20261124-aarch64-apple-darwin-install_only_stripped.tar.gz"
PYTHON_URL = "https://github.com/astral-sh/python-build-standalone/releases/download/20261124/" + PYTHON_ARCHIVE
PYTHON_HASH = "296f0903cccda15a5c9d3d8393cd2204718aeffd9b095a00f812d75c7b1c0967"
FFMPEG_ARCHIVE = "ffmpeg-9.0.1.tar.xz"
FFMPEG_URL = "https://ffmpeg.org/releases/" + FFMPEG_ARCHIVE
FFMPEG_HASH = "cf38e0e28c7e5605942c4a77755349b0145804a397af37eb1fb4c77cb237f635"
MODEL_REVISION = "a4aaeec0636e6fef84abdcbe3544cb2bf7e9f6fb"
MODEL_URL = f"https://huggingface.co/mlx-community/whisper-large-v3-turbo/resolve/{MODEL_REVISION}/"
MODEL_FILES = {
    "config.json": "b34fc29e4e11e0a25e812775dd67f4dd16fc2c8eb43d28ae25ff7d660ecb6379",
    "weights.safetensors": "951ed3fc1203e6a62467abb2144a96ce7eafca8fa77e3704fdb8635ff3e7f8a6",
}


def run(*args, **kwargs):
    subprocess.run(args, check=True, **kwargs)


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def checked(path, expected):
    if digest(path) != expected:
        raise ValueError(f"Unexpected SHA256 for {path.name}")
    return path


def fetch(cache, name, url, checksum):
    item = cache / name
    if item.exists():
        return checked(item, checksum)
    with tempfile.TemporaryDirectory(dir=cache) as temp:
        download = Path(temp) / name
        run("curl", "--fail", "--location", "--retry", "2", "--output", download, url)
        checked(download, checksum).replace(item)
    return item


def extract(archive, destination):
    with tarfile.open(archive) as tar:
        tar.extractall(destination, filter="data")


def bundle_python(resources, tmp, cache, wheelhouse):
    extract(fetch(cache, PYTHON_ARCHIVE, PYTHON_URL, PYTHON_HASH), tmp)
    runtime = resources / "runtime"
    (tmp / "python").rename(runtime)
    python = runtime / "bin/python3"
    wheels = tmp / "wheel-install"
    pip = [python, "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
           "--require-hashes", "--no-compile", "--only-binary=:all:", "--target", wheels]
    if wheelhouse:
        pip += ["--no-index", "--find-links", wheelhouse]
    run(*pip, "-r", RECIPES / "requirements.lock")
    site = runtime / "lib/python3.13/site-packages"
    for child in wheels.iterdir():
        if child.name not in {"bin", "include", "share", "__pycache__"}:
            child.rename(site / child.name)
    for child in list((runtime / "bin").iterdir()):
        if child.name not in {"python3", "python3.13"}:
            child.unlink()
    for path in runtime.rglob("__pycache__"):
        shutil.rmtree(path)
    return python


def bundle_ffmpeg(resources, tmp, cache):
    archive = fetch(cache, FFMPEG_ARCHIVE, FFMPEG_URL, FFMPEG_HASH)
    recipe = RECIPES / "build_ffmpeg.sh"
    key = digest(recipe)[:16] + "-" + FFMPEG_HASH[:12]
    tools = cache / ("ffmpeg-tools-" + key)
    if not tools.exists():
        extract(archive, tmp)
        stage = tmp / "media-build"
        run("sh", recipe, stage, cwd=tmp / "ffmpeg-9.0.1")
        shutil.copytree(stage, tools, symlinks=True)
    shutil.copytree(tools, resources / "media-tools", symlinks=True)
    licenses = resources / "Notices/FFmpeg"
    licenses.mkdir(parents=True)
    shutil.copy2(archive, licenses)
    shutil.copy2(recipe, licenses)
    with tarfile.open(archive) as package:
        text = package.extractfile("ffmpeg-9.0.1/COPYING.LGPLv2.1").read()
    (licenses / "COPYING.LGPLv2.1").write_bytes(text)
    (licenses / "SOURCE.txt").write_text(
        f"FFmpeg 9.0.1, LGPL v2.1 or later. Corresponding source: {FFMPEG_URL}\n"
        f"SHA256 {FFMPEG_HASH}\nTo rebuild, extract the adjacent source archive and run\n"
        "sh /path/to/build_ffmpeg.sh /absolute/output from its root with Apple Command Line Tools.\n"
        "The ffmpeg and ffprobe executables link to the replaceable libraries in Resources/media-tools.\n"
        "A locally modified app can be re-signed for local use.\n"
    )


def bundle_model(resources, cache, source):
    target = resources / "model-turbo"
    target.mkdir()
    for name, checksum in MODEL_FILES.items():
        item = checked(source / name, checksum) if source else fetch(cache, "whisper-" + name, MODEL_URL + name, checksum)
        shutil.copyfile(item, target / name)
    shutil.copyfile(RECIPES / "WHISPER-LICENSE.txt", resources / "Notices/WHISPER-LICENSE.txt")


def configure_app(resources, binary):
    run("xcrun", "swiftc", "-swift-version", "5", "-O", "-target", "arm64-apple-macos15.0",
        ROOT / "macos/main.swift", "-o", binary)
    manifest = dict(
        CFBundleExecutable=binary.name, CFBundleIdentifier="io.github.shichaoxx.echo.v1",
        CFBundleName=APP_NAME, CFBundleDisplayName=APP_NAME, CFBundlePackageType="APPL",
        CFBundleShortVersionString=VERSION.split("-")[0], CFBundleVersion=BUILD_NUMBER,
        EchoReleaseVersion=VERSION, LSMinimumSystemVersion=MACOS_MIN, CFBundleIconFile="Echo.icns",
        NSHighResolutionCapable=True,
        NSAppTransportSecurity={"NSAllowsLocalNetworking": True},
    )
    (resources.parent / "Info.plist").write_bytes(plistlib.dumps(manifest))
    shutil.copyfile(ROOT / "macos/Echo.icns", resources / "Echo.icns")
    shutil.copyfile(ROOT / "LICENSE", resources / "LICENSE")
    (resources / "Notices/RUNTIME.txt").write_text(
        f"Standalone CPython: {PYTHON_URL}\nSHA256 {PYTHON_HASH}\n"
        "Wheel license files are in runtime/lib/python3.13/site-packages under their distributions.\n"
        f"Whisper model: mlx-community/whisper-large-v3-turbo at {MODEL_REVISION}\n"
        "Conversion of OpenAI Whisper large-v3-turbo, distributed under the MIT license.\n"
    )
    shutil.copyfile(RECIPES / "requirements.lock", resources / "Notices/requirements.lock")
    shutil.copytree(ROOT / "frontend/dist", resources / "frontend/dist")
    examples = resources / "examples"
    examples.mkdir()
    for name in ("README.md", "catalog.json"):
        shutil.copyfile(ROOT / "examples" / name, examples / name)
    for entry in json.loads((examples / "catalog.json").read_text()):
        name = entry["id"]
        if not re.fullmatch(r"[a-z0-9-]+", name):
            raise ValueError("Invalid example directory")
        (examples / name).mkdir()
        for file in ("audio.mp3", "source.json", "script.txt", "document.json"):
            shutil.copyfile(ROOT / "examples" / name / file, examples / name / file)
    # Only server sources are app code; never collect developer workspaces or logs.
    code = resources / "server"
    code.mkdir()
    for source in (ROOT / "server").glob("*.py"):
        shutil.copy2(source, code / source.name)


def native_binaries(app):
    magic = {"cffaedfe", "cefaedfe", "cafebabe", "cafebabf", "feedfacf", "feedface"}
    for path in app.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as f:
                if f.read(4).hex() in magic:
                    yield path


def validate_and_sign(app, identity):
    options = ["--force", "--sign", identity]
    if identity != "-":
        options.extend(["--options", "runtime", "--timestamp"])
    binaries = list(native_binaries(app))
    for path in binaries:
        output = subprocess.check_output(["otool", "-arch", "arm64", "-l", path], text=True)
        if "Load command 0" not in output:
            raise ValueError(f"Missing Apple Silicon load commands in {path.name}")
        for command in re.split(r"Load command \d+\n", output)[1:]:
            fields = dict(line.strip().split(None, 1) for line in command.splitlines()
                          if len(line.strip().split(None, 1)) == 2)
            kind = fields.get("cmd", "")
            if kind == "LC_BUILD_VERSION":
                if tuple(map(int, fields["minos"].split(".")))[:2] > (15, 0):
                    raise ValueError(f"{path.name} requires macOS {fields['minos']}")
            ref = fields.get("path" if kind == "LC_RPATH" else "name", "").split(" (offset ")[0]
            if not ref.startswith("/") or ref.startswith(("/usr/lib/", "/System/Library/")):
                continue
            # Wheels sometimes retain the producer's library ID or a redundant build-time
            # search path, even when every actual load command is already relative.
            if kind == "LC_ID_DYLIB":
                run("install_name_tool", "-id", "@rpath/" + path.name, path)
            elif kind == "LC_RPATH":
                run("install_name_tool", "-delete_rpath", ref, path)
            elif kind.endswith("DYLIB"):
                raise ValueError(f"Non-relocatable library in {path.name}: {ref}")
        run("codesign", *options, path, stderr=subprocess.PIPE)
    run("codesign", *options, app)
    run("codesign", "--verify", "--deep", "--strict", app)


def build(args, tmp):
    cache, dest = args.cache_dir, args.output
    cache.mkdir(parents=True, exist_ok=True)
    run("npm", "ci", "--no-audit", "--no-fund", cwd=ROOT / "frontend")
    run("npm", "run", "build", cwd=ROOT / "frontend")
    stage = tmp / "image"
    app = stage / f"{APP_NAME}.app"
    binary = app / "Contents/MacOS/Echo"
    binary.parent.mkdir(parents=True)
    resources = app / "Contents/Resources"
    resources.mkdir()
    python = bundle_python(resources, tmp, cache, args.wheelhouse)
    bundle_ffmpeg(resources, tmp, cache)
    bundle_model(resources, cache, args.model_dir)
    configure_app(resources, binary)
    validate_and_sign(app, args.identity)
    env = dict(HOME=str(tmp), PATH=f"{resources}/media-tools:/usr/bin:/bin", PYTHONDONTWRITEBYTECODE="1")
    run(python, "-EBs", "-c", "import server.http, mlx_whisper, sqlite3", cwd=resources, env=env)
    run("ffprobe", "-v", "error", "-show_entries", "format=duration", "examples/reading-room/audio.mp3",
        cwd=resources, env=env)
    (stage / "Applications").symlink_to("/Applications")
    image = tmp / (STEM + ".dmg")
    print(f"Creating disk image for {app}", flush=True)
    run("hdiutil", "create", "-fs", "HFS+", "-format", "UDZO", "-volname", APP_NAME, "-srcfolder", stage, image)
    if args.identity != "-":
        run("codesign", "--timestamp", "--sign", args.identity, image)
    if args.notary_profile:
        run("xcrun", "notarytool", "submit", image, "--keychain-profile", args.notary_profile, "--wait")
        run("xcrun", "stapler", "staple", image)
        run("xcrun", "stapler", "validate", image)
    run("hdiutil", "verify", image)
    checksum = tmp / (image.name + ".sha256")
    checksum.write_text(f"{digest(image)}  {image.name}\n")
    image.replace(dest / image.name)
    checksum.replace(dest / checksum.name)
    print(dest / image.name, flush=True)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--model-dir", type=Path, help="Use a verified local copy of the pinned public weights")
    cli.add_argument("--wheelhouse", type=Path, help="Previously downloaded wheels for an offline Python install")
    cli.add_argument("--cache-dir", type=Path, default=Path.home() / "Library/Caches/Echo-build")
    cli.add_argument("--output", type=Path, default=ROOT / "dist" / ("v" + VERSION))
    cli.add_argument("--identity", default=os.environ.get("ECHO_CODESIGN_IDENTITY") or "-")
    cli.add_argument("--notary-profile", default=os.environ.get("ECHO_NOTARY_PROFILE"))
    args = cli.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        cli.error("An Apple Silicon Mac is required")
    if args.notary_profile and args.identity == "-":
        cli.error("Notarization requires a Developer ID signing identity")
    args.output.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory(dir=args.output, prefix=".build-") as tmp:
        build(args, Path(tmp))
