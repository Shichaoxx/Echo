"""Local interview metadata and project groups; never rename or relocate source media."""
from echo.paths import ensure_data_directory
from datetime import date
import json
import os
from pathlib import Path
import re
import tempfile
import uuid


def read_store(data: Path, name: str):
    path = data / name
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{name} 格式错误，请恢复文件后重试。")
    return value


def write_store(data: Path, name: str, value: dict):
    ensure_data_directory(data)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=data, delete=False) as f:
            temporary = f.name
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(temporary, data / name)
    finally:
        if temporary and Path(temporary).exists():
            Path(temporary).unlink()


def inferred_metadata(item: dict):
    title = item["title"].split(" / ")[0]
    parts = re.split(r"\s*(?:——|—{1}|--+)\s*", title, maxsplit=1)
    subject = parts[-1].strip()
    company = "未分类"
    for pattern, name in [(r"PDD|拼多多", "拼多多"), (r"快手", "快手"), (r"阿里|淘天", "阿里"),
                          (r"字节|ByteDance", "字节跳动"), (r"腾讯", "腾讯"), (r"百度", "百度"), (r"美团", "美团"), (r"携程|Trip\.com", "携程")]:
        if re.search(pattern, subject, re.I):
            company = name
            break
    round_match = re.search(r"(?:[一二三四五六七八九十\d]+面|HR\s*面|终面|初面)", subject, re.I)
    day = ""
    match = re.search(r"(?:(\d{4})\s*[年./-]\s*)?(\d{1,2})\s*(?:月|[./-])\s*(\d{1,2})(?:日)?", parts[0])
    if match:
        year, month, number = match.groups()
        try:
            date(int(year or 2000), int(month), int(number))
            day = f"{int(month):02d}-{int(number):02d}"
            if year:
                day = year + "-" + day
        except ValueError:
            pass
    return dict(company=company, round=round_match.group() if round_match else "轮次未标注",
                date=day, tags="", archived=False)


def metadata(item: dict, store: dict):
    return {**inferred_metadata(item), **store.get("interviews", {}).get(item["id"], {})}


def update_metadata(data: Path, key: str, **fields):
    if "date" in fields:
        value = fields["date"].strip()
        if value:
            if re.fullmatch(r"\d{2}-\d{2}", value):
                date.fromisoformat("2000-" + value)
            elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                date.fromisoformat(value)
            else:
                raise ValueError("日期请填写 YYYY-MM-DD 或 MM-DD；未知可留空。")
        fields["date"] = value
    store = read_store(data, "library.json")
    if fields.get("project_id") and fields["project_id"] not in store.get("projects", {}):
        raise ValueError("所选项目分组不存在，请刷新后重试。")
    store.setdefault("interviews", {}).setdefault(key, {}).update(fields)
    write_store(data, "library.json", store)


def manage_project(data, value):
    """Project membership is local metadata; removing a group never removes files."""
    store = read_store(data, "library.json")
    projects = store.setdefault("projects", {})
    operation, key = value.get("operation"), value.get("id")
    if operation not in ("create", "rename", "delete"):
        raise ValueError("未知项目分组操作")
    if operation != "create" and (not isinstance(key, str) or key not in projects):
        raise ValueError("项目分组不存在")
    if operation == "delete":
        del projects[key]
        for meta in store.get("interviews", {}).values():
            if meta.get("project_id") == key:
                meta["project_id"] = ""
    else:
        name = value.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise ValueError("分组名称请填写 1–80 个字符")
        name = name.strip()
        if any(p["name"] == name and i != key for i, p in projects.items()):
            raise ValueError("已有同名项目分组")
        projects[key if operation == "rename" else uuid.uuid4().hex[:12]] = {"name": name}
    write_store(data, "library.json", store)
    return projects
