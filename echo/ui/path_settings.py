"""Directory preferences and explicit media import for the local workspace."""
import os
from pathlib import Path

import streamlit as st

from echo import paths


def render_path_settings(data: Path, scan) -> None:
    source_root = paths.source_directory(data)
    output_root = paths.output_directory(data)
    saved = st.session_state.pop("path_settings_saved", False)
    # Normalize saved paths before creating this rerun's widgets; preserve drafts
    # during unrelated page interactions.
    for key, root in (("source-directory", source_root), ("output-directory", output_root)):
        if saved or key not in st.session_state:
            st.session_state[key] = str(root)

    with st.expander("导入与目录设置"):
        source = st.text_input(
            "录音 / 录屏输入目录", key="source-directory",
            disabled=bool(os.environ.get("INTERVIEW_SOURCE_DIR")),
            help="扫描此目录中的现有录音和录屏，原文件保留在原处。",
        )
        output = st.text_input(
            "转写结果输出目录", key="output-directory",
            disabled=bool(os.environ.get("INTERVIEW_OUTPUT_DIR")),
            help="新的转写版本保存在这里；历史结果保留当前位置。",
        )
        for variable, label in (("INTERVIEW_SOURCE_DIR", "输入目录"),
                                ("INTERVIEW_OUTPUT_DIR", "输出目录")):
            if os.environ.get(variable):
                st.caption(f"{label}由环境变量 {variable} 指定。")

        if st.button("保存目录设置", key="save-directories", width="stretch"):
            try:
                paths.save_directories(data, source, output)
            except (OSError, ValueError) as exc:
                st.error(str(exc))
            else:
                st.session_state["path_settings_saved"] = True
                st.rerun()

        unsaved = source.strip() != str(source_root) or output.strip() != str(output_root)
        if unsaved:
            st.caption("目录修改后请先保存，再扫描。")
        if st.button("扫描新增录屏", type="primary", width="stretch", disabled=unsaved):
            try:
                with st.spinner("正在核对录音与录屏…"):
                    counts = scan(source_root)
                st.session_state["scan_message"] = (
                    f"新增 {counts['added']} 场 · 更新 {counts['changed']} 场"
                    f" · 等待稳定 {counts['deferred']} 场"
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        st.caption("新增录音与录屏独立建档，已有版本与笔记始终保留。")
        st.caption("修改输出目录仅影响之后开始的转写，历史结果保留当前位置。")

    if saved:
        st.success("目录设置已保存。历史结果保留当前位置。")
    if "scan_message" in st.session_state:
        st.success(st.session_state.pop("scan_message"))
