"""Install Echo as a per-user macOS login service for the current checkout."""
import os
from pathlib import Path
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LABEL = "io.github.shichaoxx.echo.workbench"


def main():
    if sys.platform != "darwin":
        raise SystemExit("此安装方式需要 macOS。")
    python = ROOT / ".venv/bin/python"
    if not os.access(python, os.X_OK):
        raise SystemExit("请先按 README 安装 .venv。")
    agents = Path.home() / "Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    plist = agents / (LABEL + ".plist")
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    config = {
        "Label": LABEL,
        "ProgramArguments": [str(python), str(ROOT / "workbench.py"), "--port", "18770"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 30,
        "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
        "StandardErrorPath": str(Path.home() / "Library/Logs/Echo-workbench.log"),
    }
    content = plistlib.dumps(config)
    if plist.exists() and plist.read_bytes() != content:
        raise SystemExit(f"已有其他 Echo 服务配置。请先按 README 停用旧服务并移走 {plist}。")
    if not plist.exists():
        with plist.open("xb") as target:
            target.write(content)
    loaded = subprocess.run(["launchctl", "print", service], capture_output=True)
    if loaded.returncode:
        subprocess.run(["launchctl", "bootstrap", domain, str(plist)], check=True)
    print("Echo 登录服务已注册。工作台地址 http://127.0.0.1:18770/")


if __name__ == "__main__":
    main()
