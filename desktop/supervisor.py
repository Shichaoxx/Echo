"""Own one desktop backend process group and reap it when its window exits."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def emit(event, **fields):
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def stop_backend(process, grace=4):
    """Only signal the group created by this supervisor, including GPU workers."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        process.poll()  # Reap the group leader so it does not appear alive forever.
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            # On macOS a group can be briefly unprobeable during child reaping.
            # Keep waiting; do not confuse this transient state with completion.
            pass
        time.sleep(0.05)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, default=os.getppid())
    args = parser.parse_args()
    project = args.project.expanduser().resolve()
    if not (project / "app.py").is_file() or not (project / "echo/paths.py").is_file():
        emit("error", message="请选择 Echo v1.1 开发版的项目文件夹。")
        return 1
    sys.path.insert(0, str(project))
    from echo import paths
    if not hasattr(paths, "child_starts_session"):
        emit("error", message="请先将 Echo 项目切换到 desktop/v1.1.0 开发分支。")
        return 1
    stopping = False

    def request_stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    process = None
    lock = None
    log = None
    try:
        data = paths.ensure_data_directory(paths.data_directory(project))
        lock = (data / "desktop.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("这个工作空间已在另一个 Echo 窗口中打开。") from None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {**paths.child_environment(data), "ECHO_DESKTOP_MANAGED": "1",
               "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), "/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "/usr/bin:/bin")])
        log = (data / "desktop.log").open("ab", buffering=0)
        command = [sys.executable, "-m", "streamlit", "run", str(project / "app.py"),
                   "--server.address", "127.0.0.1", "--server.port", str(port),
                   "--server.headless", "true", "--browser.gatherUsageStats", "false",
                   "--server.fileWatcherType", "none"]
        process = subprocess.Popen(command, cwd=project, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True)
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 45
        announced = False
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while not stopping:
            if os.getppid() != args.parent_pid:
                break
            if process.poll() is not None:
                raise RuntimeError("本地服务已退出，请查看工作空间数据目录中的 desktop.log。")
            paths.ensure_data_directory(data)
            if not announced:
                try:
                    with opener.open(url + "/_stcore/health", timeout=0.5) as response:
                        healthy = response.status == 200 and response.read(16).strip() == b"ok"
                    if healthy:
                        emit("ready", url=url)
                        announced = True
                except (OSError, urllib.error.URLError):
                    pass
                if not announced and time.monotonic() > deadline:
                    raise RuntimeError("本地服务启动超时，请检查工作空间的 Python 环境。")
            time.sleep(0.2)
        return 0
    except Exception as exc:
        emit("error", message=str(exc))
        return 1
    finally:
        if process is not None:
            stop_backend(process)
        if log is not None:
            log.close()
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
