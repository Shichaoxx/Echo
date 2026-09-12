"""Local profile and library views; never rename or relocate source media."""
from echo.paths import ensure_data_directory
from collections import OrderedDict
from datetime import date
import json
import os
from pathlib import Path
import re
import tempfile


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


PROFILE_FIELDS = {"name": "姓名 / 昵称", "role": "目标岗位", "resume": "简历版本 / 本地路径",
                  "background": "项目与实习背景", "focus": "当前练习重点"}


def save_profile(data: Path, fields: dict):
    profile = read_store(data, "profile.json")
    profile.update({key: str(fields.get(key, "")).strip() for key in PROFILE_FIELDS})
    write_store(data, "profile.json", profile)
    return profile


def profile_context(profile: dict):
    lines = ["# 个人 Profile", "", "以下为用户填写的背景资料；复盘中的项目事实仍需结合简历与原始材料核对。", ""]
    for key, label in PROFILE_FIELDS.items():
        lines += [f"## {label}", str(profile.get(key, "")).strip() or "未填写", ""]
    return "\n".join(lines)


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
    store.setdefault("interviews", {}).setdefault(key, {}).update(fields)
    write_store(data, "library.json", store)


def save_view(data: Path, view: str):
    if view not in ("时间", "公司"):
        raise ValueError("未知整理方式")
    store = read_store(data, "library.json")
    store["view"] = view
    write_store(data, "library.json", store)


def date_key(meta: dict):
    day = meta["date"]
    return (len(day) == 10, day)


def group_items(items: list, store: dict, view: str):
    """Explicit-year dates first; dates with unknown year kept in separate groups."""
    ordered = sorted(items, key=lambda item: (date_key(metadata(item, store)), item["title"], item["id"]), reverse=True)
    if view == "公司":
        ordered.sort(key=lambda item: (metadata(item, store)["company"] == "未分类", metadata(item, store)["company"]))
    groups = OrderedDict()
    for item in ordered:
        meta = metadata(item, store)
        day = meta["date"]
        group = meta["company"] if view == "公司" else (
            day[:7].replace("-", " 年 ") + " 月" if len(day) == 10 else
            "年份未标注 · " + day[:2] + " 月" if day else "日期未标注")
        groups.setdefault(group, []).append(item)
    return groups
