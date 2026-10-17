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


def source_directory(data, *, required=True):
    """Return an explicitly configured folder; a new workspace has no selection."""
    value = os.environ.get("INTERVIEW_SOURCE_DIR") or _config(data).get("source")
    legacy = Path(data) / "source_path.txt"
    if not value and legacy.is_file():
        value = legacy.read_text(encoding="utf-8").strip()
    if value:
        return _resolve(value, data)
    if required:
        raise ValueError("请先选择面试文件夹，再扫描录音与录屏。")
    return None


def output_directory(data):
    config = _config(data)
    value = os.environ.get("INTERVIEW_OUTPUT_DIR") or (config.get("output") if config.get("output_mode") != "default" else None)
    if value:
        return _resolve(value, data)
    source = source_directory(data, required=False)
    if source is not None:
        return source / "echo"
    return Path(data).resolve() / "outputs"


def source_exclusions(data, source):
    """Generated media and the local ledger are not interview source material."""
    source = Path(source).expanduser().resolve()
    roots = [output_directory(data).resolve(), source / "echo", source / "echos"]
    if Path(data).resolve() != source:
        roots.append(Path(data).resolve())
    return tuple(root for root in roots if root.is_relative_to(source))


def media_in_source(data, media, *, source=None):
    """Check active-folder membership without deleting records from other folders."""
    source = Path(source).expanduser().resolve() if source is not None else source_directory(data, required=False)
    if source is None:
        return False
    media = Path(media).expanduser().resolve()
    return media.is_relative_to(source) and not any(media.is_relative_to(root) for root in source_exclusions(data, source))


def mirrored_output(data, media, output_root):
    source = source_directory(data, required=False)
    parent = Path(media).resolve().parent
    try:
        if source is None:
            raise ValueError("No folder selected")
        relative = parent.relative_to(source)
    except ValueError:
        # Individually imported media outside the library uses its containing folder.
        relative = Path(parent.name)
    if _config(data).get("output_layout") == "company":
        from echo.library import inferred_metadata
        title = Path(media).stem if relative == Path('.') else parent.name
        company = inferred_metadata({"title": title})["company"]
        # Preserve the source hierarchy, adding the company only once.
        if relative == Path('.'):
            relative = Path(Path(media).stem)
        if relative.parts[0] != company:
            relative = Path(company) / relative
    destination = Path(output_root).resolve() / relative
    if not destination.resolve().is_relative_to(Path(output_root).resolve()):
        raise ValueError("输出目录中的链接指向了输出根目录之外")
    return destination


def _portable(path, data):
    # Sibling input folders continue to work when their containing workspace moves.
    if path.is_relative_to(Path(data).resolve().parent):
        return os.path.relpath(path, Path(data).resolve())
    return str(path)


def save_directories(data, source, output, *, default_output=False, output_layout=None):
    if source is None or output is None or not str(source).strip() or not str(output).strip():
        raise ValueError("输入目录和输出目录不能为空")
    source, output = _resolve(source, data), _resolve(output, data)
    if default_output:
        output = source / "echo"
    if not source.is_dir():
        raise ValueError(f"输入目录不存在：{source}")
    if source.is_relative_to(output):
        raise ValueError("输出目录不能是输入目录本身或其上级，请选择独立文件夹。")
    if output.exists() and not output.is_dir():
        raise ValueError("输出路径必须是文件夹")
    if not output.parent.is_dir() and output.parent != Path(data).resolve():
        raise ValueError("请先创建输出目录的上级文件夹")
    for name, requested in (("INTERVIEW_SOURCE_DIR", source), ("INTERVIEW_OUTPUT_DIR", output)):
        if os.environ.get(name) and _resolve(os.environ[name], data) != requested:
            raise ValueError(f"{name} 正在覆盖此目录，请先调整启动环境变量")
    ensure_data_directory(data)
    value = _config(data)
    layout = output_layout if output_layout is not None else value.get("output_layout", "mirror")
    if layout not in ("mirror", "company"):
        raise ValueError("未知输出目录结构")
    value.update({"schema_version": 1, "source": _portable(source, data), "output": _portable(output, data), "output_mode": "default" if default_output else "custom", "output_layout": layout})
    if value.get('library_root'):
        value['source'] = os.path.relpath(source, Path(data).resolve())
        value['output'] = os.path.relpath(output, Path(data).resolve())
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
