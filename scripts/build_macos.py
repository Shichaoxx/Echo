"""Build the local-development macOS host; no recordings or runtime are bundled."""
import argparse
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "Echo.app")
    args = parser.parse_args()
    output = args.output.expanduser().resolve()
    if output.exists():
        raise SystemExit(f"Output already exists; choose a new path: {output}")
    version = (ROOT / "VERSION").read_text().strip()
    module_cache = ROOT / ".cache" / "swift"
    module_cache.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="echo-build-") as temporary:
        build = Path(temporary)
        bundle = build / "Echo.app"
        binary = bundle / "Contents/MacOS/Echo"
        resources = bundle / "Contents/Resources"
        binary.parent.mkdir(parents=True)
        resources.mkdir(parents=True)
        subprocess.run(["xcrun", "swiftc", str(ROOT / "desktop/macos/Main.swift"),
                        "-o", str(binary), "-target", "arm64-apple-macosx13.0", "-O",
                        "-module-cache-path", str(module_cache),
                        "-framework", "AppKit", "-framework", "WebKit"], check=True)
        icon = build / "icon"
        subprocess.run(["xcrun", "swiftc", str(ROOT / "desktop/macos/Icon.swift"), "-o", str(icon),
                        "-module-cache-path", str(module_cache), "-framework", "AppKit"], check=True)
        master = build / "icon.png"
        subprocess.run([str(icon), str(master)], check=True)
        # Modern macOS accepts PNG-backed icon chunks; the system scales this
        # 1024px master for all Dock/Finder sizes without a separate iconutil pass.
        png = master.read_bytes()
        chunk = struct.pack(">4sI", b"ic10", len(png) + 8) + png
        (resources / "Echo.icns").write_bytes(struct.pack(">4sI", b"icns", len(chunk) + 8) + chunk)
        metadata = plistlib.loads((ROOT / "desktop/macos/Info.plist").read_bytes())
        metadata["CFBundleShortVersionString"] = version.split("-", 1)[0]
        metadata["CFBundleVersion"] = "10101"
        metadata["EchoDevelopmentVersion"] = version
        (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps(metadata))
        shutil.copy2(ROOT / "desktop/supervisor.py", resources / "supervisor.py")
        subprocess.run(["codesign", "--force", "--sign", "-", str(bundle)], check=True)
        shutil.copytree(bundle, output)
    print(output)
    print("Local development build: select an Echo checkout with its configured .venv on first launch.")


if __name__ == "__main__":
    main()
