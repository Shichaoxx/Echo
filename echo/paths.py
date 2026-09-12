"""Local directory configuration shared by the CLI, workers and interfaces."""
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
_seen_directories = {}


def data_directory(root=ROOT):
    return Path(os.environ.get("INTERVIEW_DATA_DIR") or root / ".local").expanduser().absolute()


def ensure_data_directory(data):
    """Create only a new leaf; never resurrect a moved/deleted workspace."""
    data = Path(data).absolute()
    if data.is_symlink() and not data.exists():
        raise FileNotFoundError(f"数据目录链接已失效，请修正链接后重启 Echo：{data}")
    previous = _seen_directories.get(data)
    if previous is not None:
        try:
            current = data.stat()
        except FileNotFoundError:
            raise FileNotFoundError(f"数据目录已移动或删除，请重新选择目录并重启 Echo：{data}") from None
        if previous != (current.st_dev, current.st_ino):
            raise ValueError(f"数据目录已被替换，请重启 Echo：{data}")
    if not data.parent.is_dir():
        raise FileNotFoundError(f"上级目录不存在，请检查路径或失效的数据链接：{data.parent}")
    data.mkdir(exist_ok=True)
    stat = data.stat()
    _seen_directories[data] = (stat.st_dev, stat.st_ino)
    return data


def _config(data):
    path = Path(data) / "paths.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or any(k in value and not isinstance(value[k], str) for k in ("source", "output")):
        raise ValueError("目录配置格式错误，请检查 paths.json")
    return value


def _resolve(value, data):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(data) / path).resolve()


def source_directory(data):
    value = os.environ.get("INTERVIEW_SOURCE_DIR") or _config(data).get("source")
    legacy = Path(data) / "source_path.txt"
    if not value and legacy.is_file():
        value = legacy.read_text(encoding="utf-8").strip()
    return _resolve(value, data) if value else ROOT / "recordings"


def output_directory(data):
    value = os.environ.get("INTERVIEW_OUTPUT_DIR") or _config(data).get("output") or "outputs"
    return _resolve(value, data)


def archive_directory(data):
    legacy = Path(data) / "archive_path.txt"
    value = legacy.read_text(encoding="utf-8").strip() if legacy.is_file() else "archives"
    return _resolve(value or "archives", data)


def _portable(path, data):
    # Sibling input folders continue to work when their containing workspace moves.
    if path.is_relative_to(Path(data).resolve().parent):
        return os.path.relpath(path, Path(data).resolve())
    return str(path)


def save_directories(data, source, output):
    if not str(source).strip() or not str(output).strip():
        raise ValueError("输入目录和输出目录不能为空")
    source, output = _resolve(source, data), _resolve(output, data)
    if not source.is_dir():
        raise ValueError(f"输入目录不存在：{source}")
    if output.exists() and not output.is_dir():
        raise ValueError("输出路径必须是文件夹")
    if not output.parent.is_dir() and output.parent != Path(data).resolve():
        raise ValueError("请先创建输出目录的上级文件夹")
    for name, requested in (("INTERVIEW_SOURCE_DIR", source), ("INTERVIEW_OUTPUT_DIR", output)):
        if os.environ.get(name) and _resolve(os.environ[name], data) != requested:
            raise ValueError(f"{name} 正在覆盖此目录，请先调整启动环境变量")
    ensure_data_directory(data)
    value = {"schema_version": 1, "source": _portable(source, data), "output": _portable(output, data)}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=data, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        temporary.replace(Path(data) / "paths.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def child_environment(data):
    # Share the active data root. Input/output remain dynamic so saved changes reach
    # an already running dispatcher at the start of its next task.
    return {**os.environ, "INTERVIEW_DATA_DIR": str(Path(data).resolve())}


def child_starts_session():
    """A desktop supervisor owns the complete backend group until app exit."""
    return os.environ.get("ECHO_DESKTOP_MANAGED") != "1"
