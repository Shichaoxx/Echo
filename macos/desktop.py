"""Start the packaged loopback service in the current user's login session."""
import fcntl
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

RESOURCES = Path(__file__).resolve().parent
LABEL = "io.github.shichaoxx.echo.desktop"
PORT = 18770
DATA = Path.home() / "Library/Application Support/Echo"
LOG = Path.home() / "Library/Logs/Echo-desktop.log"


def ready():
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{PORT}/api/bootstrap", timeout=1) as response:
            info = json.load(response)
    except (URLError, OSError, ValueError):
        return False
    if (info.get("runtime", {}).get("python") != sys.executable or
            info.get("settings", {}).get("data") != str(DATA)):
        raise SystemExit("端口 18770 正由另一个工作台使用。请先退出旧工作台。")
    return True


def start():
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    plist = Path.home() / "Library/LaunchAgents" / (LABEL + ".plist")
    plist.parent.mkdir(exist_ok=True)
    args = [sys.executable, "-EsB", str(RESOURCES / "workbench.py"),
            "--data-dir", str(DATA), "--port", str(PORT)]
    build = plistlib.loads((RESOURCES.parent / "Info.plist").read_bytes())["CFBundleVersion"]
    config = plistlib.dumps({
        "Label": LABEL, "ProgramArguments": args, "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 20,
        "EnvironmentVariables": {"PATH": str(RESOURCES / "media-tools") + ":/usr/bin:/bin",
                                 "ECHO_DESKTOP_BUILD": build,
                                 "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
        "StandardErrorPath": str(LOG),
    })
    loaded = subprocess.run(["launchctl", "print", service], capture_output=True).returncode == 0
    if plist.is_file() and plist.read_bytes() != config and loaded:
        subprocess.run(["launchctl", "bootout", service], check=True)
        loaded = False
    if not loaded:
        ready()  # Detect an existing repository service before registering this one.
        temp = plist.with_suffix(".tmp")
        temp.write_bytes(config)
        temp.replace(plist)
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    for _ in range(60):
        if ready():
            return
        time.sleep(0.2)
    raise SystemExit(f"工作台尚未就绪，请查看 {LOG}")


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / "desktop.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        start()
