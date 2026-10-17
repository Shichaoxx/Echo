"""Explicit, folder-bound Markdown memory; status never writes user data."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

from echo import paths

BINDINGS = "memory-locations.json"
OWNER = ".echo-memory.json"
INDEX = """# Memory

本目录只保存经用户确认、适合长期复用的内容；目前尚未提炼个人结论。

- [个人画像](profile.md)：待用户确认的背景、目标与偏好。
- [专题记忆](topics/)：按需创建，记录来源、确认日期与适用边界。

单场证据和讨论保留在对应复盘文档；先提出候选更新，经用户确认后再写入这里。
"""
PROFILE = """# 个人画像

状态：待用户确认。以下为空白模板，不代表已经确认的个人事实或能力评价。

## 背景与目标

待用户确认。

## 学习与协作偏好

待用户确认。

## 已确认的背景事实

待用户确认；事实需附具体来源与确认日期。能力判断和可复用方法统一维护在 topics/。

## 确认与修订记录

尚无确认记录。
"""


class MemoryStorageError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _fail(code, message):
    raise MemoryStorageError(code, message)


def _no_links(path):
    for part in (path, *path.parents):
        if part.is_symlink():
            _fail("unsafe_path", f"Memory 路径不能包含符号链接：{part}")


def _json_file(path):
    _no_links(path)
    if not path.is_file() or path.stat().st_size > 1_000_000:
        _fail("invalid_binding", f"Memory 归属文件无效：{path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise MemoryStorageError("invalid_binding", f"Memory 归属文件无法解析：{path}") from exc
    if not isinstance(value, dict):
        _fail("invalid_binding", f"Memory 归属文件格式错误：{path}")
    return value


def _locations(data):
    binding = data / BINDINGS
    _no_links(binding)
    if not binding.exists():
        return {}
    value = _json_file(binding)
    locations = value.get("locations")
    if value.get("schema_version") != 1 or not isinstance(locations, dict):
        _fail("invalid_binding", "Memory 位置绑定格式错误，请核对 memory-locations.json。")
    for source, root in locations.items():
        if not isinstance(source, str) or not isinstance(root, str) or not Path(source).is_absolute() or not Path(root).is_absolute():
            _fail("invalid_binding", "Memory 绑定必须包含资料库和 Memory 的绝对路径。")
        if os.path.normpath(source) != source or os.path.normpath(root) != root:
            _fail("invalid_binding", "Memory 绑定路径尚未规范化，请人工核对归属。")
    return locations


def _context(data):
    data = Path(data).expanduser()
    if not data.is_dir():
        _fail("data_unavailable", "数据目录不存在或链接已失效；请恢复配置后再使用 Memory。")
    # A valid existing .local symlink is a supported Echo data-root configuration.
    data = data.resolve(strict=True)
    try:
        source = paths.source_directory(data, required=False)
    except (ValueError, OSError, RuntimeError) as exc:
        raise MemoryStorageError("source_unavailable", f"无法读取资料库配置：{exc}") from exc
    if source is None:
        return data, None, None, {}, False
    if not source.is_dir():
        _fail("source_unavailable", "已配置的面试文件夹不可用；请恢复或重新选择资料库。")
    source = source.resolve(strict=True)
    locations = _locations(data)
    bound = str(source) in locations
    if bound:
        root = Path(locations[str(source)])
    else:
        try:
            output = paths.output_directory(data)
        except (ValueError, OSError, RuntimeError) as exc:
            raise MemoryStorageError("output_unavailable", f"无法读取输出目录配置：{exc}") from exc
        _no_links(output)
        if (output.exists() and not output.is_dir()) or not output.parent.is_dir():
            _fail("output_unavailable", "输出目录或其上级不可用，请先恢复目录配置。")
        root = output / "memory"
    _no_links(root)
    for other, location in locations.items():
        if other != str(source) and Path(location) == root:
            _fail("location_conflict", "此 Memory 位置已绑定另一资料库，请为当前资料库选择独立输出目录。")
    return data, source, root, locations, bound


def _check_tree(data, source, root, bound):
    if not root.exists():
        if bound:
            _fail("bound_directory_missing", "已绑定的 Memory 目录已丢失或移动；请恢复原目录，不会自动补建。")
        return False
    if not root.is_dir():
        _fail("unowned_directory", "Memory 目标已存在且不是文件夹，请人工核对。")
    marker = root / OWNER
    _no_links(marker)
    if not marker.is_file():
        _fail("unowned_directory", "Memory 目录已存在但没有归属标记，不会接管或覆盖。")
    owner = _json_file(marker)
    expected = {"schema_version": 1, "source_directory": str(source), "data_directory": str(data)}
    from echo import storage
    book = storage.manifest(data)
    if book is not None:
        expected['library_id'] = book['id']
    if owner != expected:
        _fail("location_conflict", "Memory 目录的归属与当前资料库不一致，不会读取或改写。")
    # Topic contents stay private; inspect entry types only, never Markdown bodies.
    for folder, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            _no_links(Path(folder) / name)
    for name, directory in (("MEMORY.md", False), ("profile.md", False), ("topics", True)):
        entry = root / name
        if not (entry.is_dir() if directory else entry.is_file()):
            _fail("incomplete_memory", f"Memory 缺少必要条目 {name}；请恢复文件，不会覆盖或补写已有目录。")
    return True


def _snapshot(source, root, initialized):
    return {"root": str(root) if root else None,
            "index_path": str(root / "MEMORY.md") if root else None,
            "profile_path": str(root / "profile.md") if root else None,
            "topics_path": str(root / "topics") if root else None,
            "source_directory": str(source) if source else None,
            "initialized": initialized,
            "warnings": [] if initialized else ["Memory 尚未初始化。" if source else "请先选择面试文件夹，再启用 Memory。"]}


def status(data):
    """Return the pinned location without creating directories or reading notes."""
    try:
        data, source, root, _, bound = _context(data)
        complete = _check_tree(data, source, root, bound) if root else False
        return _snapshot(source, root, bool(bound and complete))
    except OSError as exc:
        raise MemoryStorageError("io_error", f"无法访问 Memory 目录：{exc}") from exc


@contextmanager
def _lock(data):
    lock = data / "memory-locations.lock"
    _no_links(lock)
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _fail("unsafe_path", "Memory 锁路径必须是普通文件。")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def _write_bindings(data, locations):
    target = data / BINDINGS
    _no_links(target)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=data, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({"schema_version": 1, "locations": locations}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def initialize(data, *, expected_source=None, expected_root=None):
    """Explicitly create empty templates and pin their location, without overwrites."""
    try:
        data, source, root, _, bound = _context(data)
        if source is None:
            _fail("source_required", "请先选择面试文件夹，再初始化 Memory。")
        _check_tree(data, source, root, bound)
        initial_source, initial_root = source, root
        with _lock(data):
            # Another CLI/Web initializer may have added a source while we waited.
            data, source, root, locations, bound = _context(data)
            if source is None:
                _fail("source_required", "资料库选择已变化，请重新选择后初始化 Memory。")
            if (source != initial_source or root != initial_root
                    or expected_source is not None and str(source) != str(expected_source)
                    or expected_root is not None and str(root) != str(expected_root)):
                _fail("configuration_changed", "资料库或 Memory 位置已变化，请刷新后重新初始化。")
            complete = _check_tree(data, source, root, bound)
            if bound:
                return _snapshot(source, root, complete)
            if not complete:
                root.parent.mkdir(exist_ok=True)
                root.mkdir(mode=0o700)
                owner = {"schema_version": 1, "source_directory": str(source), "data_directory": str(data)}
                from echo import storage
                book = storage.manifest(data)
                if book is not None:
                    owner['library_id'] = book['id']
                for name, body in ((OWNER, json.dumps(owner, ensure_ascii=False, indent=2) + "\n"),
                                   ("MEMORY.md", INDEX), ("profile.md", PROFILE)):
                    with (root / name).open("x", encoding="utf-8") as handle:
                        handle.write(body)
                (root / "topics").mkdir()
            locations[str(source)] = str(root)
            _write_bindings(data, locations)
            return _snapshot(source, root, True)
    except OSError as exc:
        raise MemoryStorageError("io_error", f"无法初始化 Memory：{exc}") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description="查询或显式初始化当前面试资料库的本地 Memory")
    parser.add_argument("operation", choices=("status", "init"))
    parser.add_argument("--data-dir", type=Path, default=paths.data_directory())
    args = parser.parse_args(argv)
    try:
        result = initialize(args.data_dir) if args.operation == "init" else status(args.data_dir)
    except MemoryStorageError as exc:
        print(json.dumps({"error": str(exc), "code": exc.code}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
