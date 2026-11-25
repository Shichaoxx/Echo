"""Validate agent documents and resolve every factual block to transcript evidence."""

import re
import reprlib

from .store import Problem, string


def ids(values, allowed, minimum, maximum, location):
    if not isinstance(values, list) or not minimum <= len(values) <= maximum:
        raise Problem(f"{location} 应包含 {minimum} 至 {maximum} 个正文块 ID。")
    seen = set()
    for position, key in enumerate(values):
        field = f"{location}[{position}]"
        if not isinstance(key, str) or key not in allowed:
            raise Problem(f"{field} 的正文块 {reprlib.repr(key)} 不存在，请核对 blocks 中的 ID。")
        if key in seen:
            raise Problem(f"{field} 重复引用了正文块 {key}，请删除重复项。")
        seen.add(key)
    return values


def content(block, value):
    block["title"] = string(value.get("title"), "标题", 300)
    block["text"] = string(value.get("text"), "正文")
    if block["type"] == "quote" and not any(block["text"] in e["text"] for e in block["evidence"]):
        indices = ", ".join(str(ref["index"]) for ref in block["evidence"])
        raise Problem(f"金句块 {block['id']} 的正文未逐字出现在片段 [{indices}] 中，请核对引文或引用索引。")
    if "interpretation" in value:
        if block["type"] != "quote":
            raise Problem("仅金句支持独立解读。")
        interpretation = string(value["interpretation"], "金句解读", 2000, blank=True)
        if interpretation:
            block["interpretation"] = interpretation
        else:
            block.pop("interpretation", None)


def validate(value, segments):
    entries = value.get("blocks")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 300:
        raise Problem("纪要应包含 1 至 300 个内容块。")
    by_index = {s["index"]: s for s in segments}
    blocks = {}
    size = 0
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise Problem(f"blocks[{position}] 应为内容块对象。")
        if set(entry) - {"id", "type", "title", "text", "parent_id", "interpretation", "evidence"}:
            raise Problem(f"blocks[{position}] 包含不支持的字段，请核对交接格式。")
        key = string(entry.get("id"), f"blocks[{position}].id", 64)
        kind = string(entry.get("type"), "类型", 20)
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]+", key)
            or key in blocks
            or kind not in {"summary", "chapter", "followup", "decision", "quote"}
        ):
            raise Problem(
                f"blocks[{position}] 的 ID {reprlib.repr(key)} 重复、格式无效，或类型 {reprlib.repr(kind)} 不支持。"
            )
        refs = entry.get("evidence")
        if not isinstance(refs, list) or not 1 <= len(refs) <= 100:
            raise Problem(f"块 {key} 的 evidence 需要 1 至 100 个片段索引。")
        seen = set()
        for index_position, index in enumerate(refs):
            field = f"块 {key} 的 evidence[{index_position}]"
            if type(index) is not int or index not in by_index:
                raise Problem(
                    f"{field} 指定了无效片段索引 {reprlib.repr(index)}，请使用本次交接中列出的整数索引。"
                )
            if index in seen:
                raise Problem(f"{field} 重复引用片段索引 {index}，请删除重复项。")
            seen.add(index)
        snapshots = [dict(by_index[i]) for i in refs]
        size += sum(len(ref["text"]) for ref in snapshots)
        if size > 2_000_000:
            raise Problem("纪要引用内容过多。")
        block = {"id": key, "type": kind, "evidence": snapshots}
        if kind == "followup":
            block["parent_id"] = string(entry.get("parent_id"), "所属章节", 64)
        elif "parent_id" in entry:
            raise Problem("只有追问可指定所属章节。")
        content(block, entry)
        blocks[key] = block
    kinds = {b["type"] for b in blocks.values()}
    if not {"summary", "chapter"}.issubset(kinds):
        raise Problem("纪要需要摘要和章节。")
    for b in blocks.values():
        if b["type"] == "followup" and blocks.get(b["parent_id"], {}).get("type") != "chapter":
            raise Problem(f"追问 {b['id']} 的 parent_id {b['parent_id']} 未对应有效的 chapter。")
    doc = {"blocks": list(blocks.values())}
    if "overview" in value:
        overview = value["overview"]
        if (
            not isinstance(overview, dict)
            or not isinstance(overview.get("groups"), list)
            or not 1 <= len(overview["groups"]) <= 8
        ):
            raise Problem("概览需要 1 至 8 个分组。")
        groups = []
        for pos, g in enumerate(overview["groups"]):
            location = f"overview.groups[{pos}]"
            if not isinstance(g, dict):
                raise Problem(f"{location} 应为包含标题和正文块 ID 的分组。")
            groups.append(
                {
                    "title": string(g.get("title"), "分组标题", 120),
                    "block_ids": ids(g.get("block_ids"), blocks, 1, 12, location + ".block_ids"),
                }
            )
        doc["overview"] = {"title": string(overview.get("title"), "概览标题", 120), "groups": groups}
    if "comparison" in value:
        spec = value["comparison"]
        if not isinstance(spec, dict):
            raise Problem("对照表格式无效。")
        doc["comparison"] = {
            "title": string(spec.get("title"), "对照表标题", 120),
            "block_ids": ids(spec.get("block_ids"), blocks, 2, 24, "comparison.block_ids"),
        }
    return doc


def apply_edits(doc, edits):
    if not isinstance(edits, list) or not 1 <= len(edits) <= len(doc["blocks"]):
        raise Problem("请选择要修订的内容块。")
    by_id = {b["id"]: b for b in doc["blocks"]}
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) - {"id", "title", "text", "interpretation"}:
            raise Problem("修订只可更新标题、正文和引文解读。")
        block = by_id.pop(string(edit.get("id"), "块 ID", 64), None)
        if block is None:
            raise Problem("修订内容块重复或不存在。")
        content(block, edit)
