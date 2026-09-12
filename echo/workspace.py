"""Local interview workspace. Start through start.command."""
from html import escape
import json
from pathlib import Path
import re
import subprocess
import sys

import streamlit as st
from echo.ui.queue_panel import show_progress
from echo.ui.player import render_player
from echo.ui.transcript import render_transcript
from echo.ui.common import html, local_time
from echo import interview as core
from echo import queue_jobs as queue
from echo import workspace_review as reviews
from echo import library
from echo import paths
from echo.ui.library_panel import render_navigation, render_archive_settings
from echo.ui.path_settings import render_path_settings
from echo.ui.profile import render_profile
from echo.transcription_ui import scope_label, media_duration, range_error
from echo.asr_models import MODELS, LANGUAGES, model_name, model_ready, load_default_model, save_default_model
from echo.themes import THEMES, load_theme, save_theme, theme_css

# Keep drafts even when Streamlit removes widgets hidden by a model/interview switch.
for draft_key, draft_value in st.session_state.get("setting_drafts", {}).items():
    if draft_key not in st.session_state:
        st.session_state[draft_key] = draft_value

st.set_page_config(page_title="回声 · Echo", page_icon="◉", layout="wide")
try:
    paths.ensure_data_directory(core.DATA)
    source_root = paths.source_directory(core.DATA)
    paths.output_directory(core.DATA)
except (OSError, ValueError) as exc:
    st.error(f"无法访问本地数据目录或路径设置：{exc}")
    st.stop()
if "appearance" not in st.session_state:
    st.session_state["appearance"] = load_theme(core.DATA)
st.markdown(theme_css(st.session_state["appearance"]), unsafe_allow_html=True)
st.markdown((core.ROOT / "assets/style.css").read_text(encoding="utf-8"), unsafe_allow_html=True)
st.markdown((core.ROOT / "assets/workspace.css").read_text(encoding="utf-8"), unsafe_allow_html=True)



def highlighted(text, query):
    if not query:
        return escape(text)
    return "".join(f"<mark>{escape(part)}</mark>" if i % 2 else escape(part)
                   for i, part in enumerate(re.split(f"({re.escape(query)})", text, flags=re.I)))


try:
    library_store = library.read_store(core.DATA, "library.json")
    profile = library.read_store(core.DATA, "profile.json")
except (OSError, ValueError) as exc:
    st.error(f"无法读取本地资料设置：{exc}")
    st.stop()
items = core.interviews()
lookup = {i["id"]: i for i in items}
all_runs = {i["id"]: core.runs(i["id"]) for i in items}
worker_busy = core.worker_active()
run_snapshot = {(r["id"], r["state"]) for rs in all_runs.values() for r in rs}
queue_snapshot = queue.jobs()
if any(j["state"] == "queued" for j in queue_snapshot):
    queue.ensure_dispatcher()
def usable_result(r, item):
    return (r["state"] == "completed" and r.get("source_sha256") == item["sha256"]
            and bool(r.get("output_dir")) and Path(r["output_dir"], "transcript.json").is_file())


def complete_scope(r, item):
    opts = json.loads(r["options"])
    return usable_result(r, item) and not opts.get("start", 0) and not opts.get("limit", 0)


def interview_status(key):
    runs = all_runs[key]
    if worker_busy and any(r["state"] == "running" for r in runs):
        return "转写中"
    if any(j["interview_id"] == key and j["state"] == "queued" for j in queue_snapshot):
        return "已排队"
    if any(complete_scope(r, lookup[key]) for r in runs):
        return "整场完成"
    if any(usable_result(r, lookup[key]) for r in runs):
        return "仅试转"
    return "待转写"

with st.sidebar:
    selected_key = render_navigation(core.DATA, items, library_store, profile, interview_status)
    workspace_view = st.session_state["workspace_view"]
    st.caption(f"全局 · {'转写中' if worker_busy else '空闲'} · {sum(j['state'] == 'queued' for j in queue_snapshot)} 项等待")
    with st.expander("外观设置"):
        st.selectbox("界面主题", list(THEMES), format_func=lambda key: THEMES[key][0],
                     key="appearance", on_change=lambda: save_theme(core.DATA, st.session_state["appearance"]))
        st.caption("已应用并保存：" + THEMES[st.session_state["appearance"]][0])
    st.divider()
    render_path_settings(core.DATA, core.scan)
    st.button("↻  刷新资料库", width="stretch")
    html('<div class="local-card"><span class="status-dot"></span> 本地工作空间<p>录屏与个人笔记保存在这台 Mac<br>多模型 · 本地转写</p></div>')

if workspace_view == "个人 Profile":
    render_profile(core.DATA, profile)
    st.stop()

if not items:
    html('<div class="empty-state"><div class="eyebrow">YOUR FIRST INTERVIEW</div><h1>从第一场面试开始。</h1><p>在左侧选择录屏目录，将每一次对话、思考和复盘收录在一起。</p></div>')
    st.stop()

if not selected_key:
    if workspace_view == "全局队列":
        show_progress(True, queue_snapshot=queue_snapshot, run_snapshot=run_snapshot, worker_busy=worker_busy,
                      lookup=lookup, state="pending", selected_key=None)
    else:
        st.info("当前视图没有面试。可清空搜索、切换档案范围，或在目录设置中扫描新增文件。")
    st.stop()

item = lookup[selected_key]
history = all_runs[selected_key]
completed = [r for r in history if r["state"] == "completed"]
state = history[0]["state"] if history else "pending"
if state == "running" and not worker_busy:
    state = "interrupted"
if state != "running" and any(j["state"] == "queued" and j["interview_id"] == selected_key for j in queue_snapshot):
    state = "queued"
state_name = {"completed": "整场完成", "running": "正在转写", "queued": "已排队", "failed": "等待重试", "interrupted": "转写已中断", "pending": "待转写"}[state]
if state == "completed" and not any(complete_scope(r, item) for r in history):
    state_name = "仅试转 · 尚无整场结果"
short_title = item["title"].split(" / ")[0]
parts = re.split(r"\s*——\s*", short_title, maxsplit=1)
date_label, title = (parts[0], parts[1]) if len(parts) == 2 else ("面试档案", short_title)
if workspace_view == "面试详情":
    meta = library.metadata(item, library_store)
    html(f'<div class="workspace-top"><span>资料库 <span class="slash">/</span> {escape(meta["company"])} <span class="slash">/</span> {escape(meta["round"])}</span><span class="private-tag">● 本地保存</span></div>')
    heading_col, manage_col = st.columns([3, 2], vertical_alignment="center")
    with heading_col:
        html(f'<div class="hero"><div><h1>{escape(title)}</h1><p>{escape(meta["date"] or date_label)} · {escape(state_name)}' + (' · 已归档' if meta['archived'] else '') + '</p></div></div>')

progress_args = dict(queue_snapshot=queue_snapshot, run_snapshot=run_snapshot, worker_busy=worker_busy,
                     lookup=lookup, state=state, selected_key=selected_key)
if workspace_view == "面试详情" and state == "running":
    with st.expander("本场转写进度", expanded=not completed):
        show_progress(False, **progress_args)
else:
    show_progress(workspace_view == "全局队列", **progress_args)
if workspace_view == "全局队列":
    st.stop()
if "submission_notice" in st.session_state:
    st.toast(st.session_state.pop("submission_notice"))

with manage_col.popover("管理这场 · 版本、转写与归档"):
    render_archive_settings(core.DATA, item, library_store)
    run = None
    result = None
    output = None
    if completed:
        run = completed[0]
        with st.expander("版本与转写设置", expanded=False):
            run = st.selectbox("查看转写版本", completed,
                format_func=lambda r: local_time(r["started_at"]) + " · " + model_name(r["model"]) + (" · 3 分钟试听" if json.loads(r["options"])["limit"] == 180 else " · 试听" if json.loads(r["options"])["limit"] else " · 从指定位置至结束" if json.loads(r["options"]).get("start", 0) else " · 完整转写") + (" · 核对版" if json.loads(r["options"]).get("based_on_run") else ""), key=f"version-{selected_key}")
            st.caption(f"源文件：{Path(item['path']).name}")
    else:
        st.caption(f"源文件：{Path(item['path']).name}")
    if run:
        st.caption("本版本模型：" + model_name(run["model"]))
        output = Path(run["output_dir"])
        if (output / "transcript.json").is_file():
            result = json.loads((output / "transcript.json").read_text(encoding="utf-8"))["result"]
        else:
            st.warning("此版本的文件暂不可用，可重新转写生成新版本。")
        if run["source_sha256"] != item["sha256"]:
            st.warning("当前查看的是旧录屏版本；源文件已有更新。")
    if history and history[0]["error"]:
        st.error(history[0]["error"])

    source_duration = None
    try:
        source_stat = Path(item["path"]).stat()
        source_duration = media_duration(item["path"], source_stat.st_size, source_stat.st_mtime_ns)
    except OSError:
        pass
    with st.expander("新建转写 / 更换模型重转", expanded=not completed):
        if "asr_model" not in st.session_state:
            st.session_state["asr_model"] = load_default_model(core.DATA)
        selected_model = st.selectbox("转写模型", list(MODELS), format_func=model_name,
            key="asr_model", on_change=lambda: save_default_model(core.DATA, st.session_state["asr_model"]))
        st.caption("已下载 · 可本地运行" if model_ready(selected_model, core.DATA) else "首次转写时下载模型")
        st.caption("用于下一次转写，已有版本保留原模型。")
        if "queue_mode" not in st.session_state:
            st.session_state["queue_mode"] = queue.enabled()
        queue_mode = st.toggle("队列模式", key="queue_mode",
            on_change=lambda: queue.set_enabled(st.session_state["queue_mode"]),
            help="开启后可连续提交多场面试，按提交顺序逐场转写。关闭只影响后续提交，已排队任务继续处理。")
        st.caption("连续提交 · 按顺序处理" if queue_mode else "单次提交 · 空闲时开始")
        spec = MODELS[selected_model]
        st.markdown("**" + spec.name + "**")
        st.caption(spec.timing + " · " + ("模型已就绪" if model_ready(selected_model, core.DATA) else spec.description))
        language = st.selectbox("录音语言", list(LANGUAGES), format_func=lambda x: LANGUAGES[x], key=f"language-{selected_key}")
        mode = st.radio("转写范围", ["完整录屏", "指定片段试转", "指定位置至结束"], horizontal=True, key=f"mode-{selected_key}")
        start, limit = 0, 0
        if mode != "完整录屏":
            start = st.number_input("起始位置（秒）", min_value=0, value=300, step=30, key=f"start-{selected_key}")
        if mode == "指定片段试转":
            limit = st.number_input("片段长度（秒）", min_value=1, max_value=3600, value=180, step=30, key=f"limit-{selected_key}")
            st.caption("建议选有实际对话的技术讨论片段；例如从 300 秒开始，避开开头候场。")
        prompt = st.text_area("技术术语 / 上下文提示", core.PROMPT, key=f"prompt-v2-{selected_key}")
        chunk_seconds, silence_filter = 25, True
        if spec.backend == "qwen":
            chunk_seconds = st.slider("音频分段时长（秒）", min_value=10, max_value=60, value=25, step=5,
                key=f"chunk-{selected_key}", help="每次交给模型的音频长度上限，整场录音都会处理。较短方便定位，较长保留更多上下文；建议 20–25 秒。")
            silence_filter = st.checkbox("跳过近乎静音的音频块", value=True, key=f"silence-{selected_key}")
            st.caption("过滤阈值为 −55 dBFS；特别轻的声音可能被跳过，可关闭。")
        force = st.checkbox("强制生成新版本，保留已有结果", key=f"force-{selected_key}")
        busy = worker_busy or "submitted_process" in st.session_state or any(j["state"] in ("queued", "running") for j in queue_snapshot)
        options_preview = dict(start=start, limit=limit)
        st.info("本次提交：" + spec.name + " · " + LANGUAGES[language] + " · " + scope_label(options_preview))
        if source_duration is not None:
            st.caption(f"原视频时长 {core.stamp(source_duration)[:8]}" + (f" · 预计处理 {min(limit or source_duration - start, source_duration - start) / 60:.1f} 分钟音频" if start < source_duration else ""))
        else:
            st.caption("暂无法读取原视频时长，提交前请自行核对范围。")
        st.caption("设置用于本次新提交，不改变正在执行或已经排队的参数；等待任务请在全局队列中修改。")
        if spec.backend == "qwen":
            st.caption(f"每块最多 {chunk_seconds} 秒 · 静音过滤{'开启' if silence_filter else '关闭'}")
        invalid_range = range_error(start, limit, source_duration)
        if invalid_range:
            st.error(invalid_range)
        drafts = st.session_state.setdefault("setting_drafts", {})
        for key in list(st.session_state):
            if key.startswith(("asr_model", "queue_mode", "language-", "mode-", "start-", "limit-", "prompt-v2-", "chunk-", "silence-", "force-")):
                drafts[key] = st.session_state[key]
        if st.button("加入转写队列" if queue_mode else "开始本地转写", type="primary", disabled=bool(invalid_range) or (busy and not queue_mode) or not Path(item["path"]).is_file()):
            if queue_mode:
                queue.submit(item["id"], dict(model=selected_model, language=language, start=start, limit=limit,
                    chunk_seconds=chunk_seconds, silence_filter=silence_filter, prompt=prompt, force=force))
                queue.ensure_dispatcher()
                st.session_state["submission_notice"] = "已加入队列，将按顺序处理。相同参数的等待任务不会重复添加。"
                st.rerun()
            try:
                paths.ensure_data_directory(core.DATA)
            except (OSError, ValueError) as exc:
                st.error(f"无法访问本地数据目录：{exc}")
                st.stop()
            command = [sys.executable, str(core.ROOT / "interview.py"), "transcribe", item["id"],
                "--model", selected_model, "--language", language, "--start", str(start), "--limit", str(limit),
                "--chunk-seconds", str(chunk_seconds), "--prompt", prompt]
            if not silence_filter:
                command.append("--no-silence-filter")
            if force:
                command.append("--force")
            with (core.DATA / "worker.log").open("ab") as log:
                st.session_state["submitted_process"] = subprocess.Popen(
                    command, cwd=core.ROOT, stdout=log, stderr=log,
                    env=paths.child_environment(core.DATA), start_new_session=paths.child_starts_session(),
                )
            st.rerun()
        if busy and not queue_mode:
            st.caption("已有任务正在运行，完成后可以提交新任务。")


segments_all = result.get("segments", []) if result else []
flag_count = sum(bool(core.review_reasons(s)) for s in segments_all)
duration = result.get("duration", max((s["end"] for s in segments_all), default=0)) if result else 0
metrics = [("本版覆盖时长", f"{duration / 60:.1f}" if result else "—", "分钟"), ("转写片段", f"{len(segments_all):,}", "段"), ("待核对", str(flag_count), "处提示"), ("历史版本", str(len(history)), "份记录")]
html('<div class="stat-grid">' + ''.join(f'<div class="stat-card"><span>{label}</span><div>{value}<small>{unit}</small></div></div>' for label, value, unit in metrics) + '</div>')

transcript_tab, review_tab, notes_tab, history_tab = st.tabs(["逐字稿与回听", "Codex 复盘与总结", "复盘笔记", "处理记录"])
with transcript_tab:
    if result:
        if result.get("source_start", 0):
            st.caption(f"当前为指定片段：{core.stamp(result['source_start'])[:8]} 起；显示原录屏时间戳。")
        show_review = st.toggle("显示复盘侧栏", value=True, key="show-review-panel")
        if show_review:
            reading, listening = st.columns([1.7, 1], gap="large")
        else:
            reading, listening = st.container(), None
        with reading:
            render_transcript(segments_all, run["id"], result.get("source_start", 0))
            with st.expander("时间戳与转写提示"):
                st.caption("Qwen 时间戳按音频块定位；高亮表示当前音频片段，不代表逐字对齐。")
                st.caption(f"本版 {flag_count} 处核对提示。技术术语建议结合原声核对。")
        if listening is not None:
            with listening:
                html('<div class="section-heading"><h3>复盘</h3><span>CODEX</span></div>')
                preview = reviews.read_document(reviews.review_path(item["path"]), kind="interview", interview_id=item["id"])
                if preview:
                    for warning in preview["warnings"]:
                        st.warning(warning)
                    st.caption("状态：" + preview["metadata"].get("status", "未标注"))
                    with st.container(height=430, border=False, key="review-preview"):
                        st.markdown(preview["body"])
                    evidence_times = sorted(set(re.findall(r"\b\d{2}:\d{2}:\d{2}\b", preview["body"])))
                    offset = result.get("source_start", 0)
                    available_times = [t for t in evidence_times if offset <= sum(int(v) * m for v, m in zip(t.split(":"), (3600, 60, 1))) < offset + duration]
                    if available_times:
                        evidence = st.selectbox("原回答时间点", available_times, key="panel-evidence-" + run["id"])
                        if st.button("回听原回答", key="panel-play-" + run["id"]):
                            st.session_state["seek-" + run["id"]] = sum(int(v) * m for v, m in zip(evidence.split(":"), (3600, 60, 1)))
                            st.session_state["seek-token-" + run["id"]] = st.session_state.get("seek-token-" + run["id"], 0) + 1
                            st.session_state["play-request-" + run["id"]] = True
                else:
                    st.info("尚无本场复盘。可在「Codex 复盘与总结」中复制说明交给 Codex。")
                with st.expander("导出与回听备用"):
                    for label, filename in [("核对稿", "review.md"), ("纯文本", "transcript.txt"), ("SRT 字幕", "transcript.srt"), ("JSON 数据", "transcript.json")]:
                        if (output / filename).is_file():
                            st.download_button(label, (output / filename).read_bytes(), file_name=filename, key=filename)
                    if (output / "audio.mp3").is_file():
                        st.download_button("下载音频备用", (output / "audio.mp3").read_bytes(), file_name="audio.mp3", mime="audio/mpeg")
    elif state == "queued":
        html('<div class="empty-state processing-empty"><div class="empty-icon">◷</div><h3>已排队，等待开始。</h3><p>轮到这场时会自动转写，完成后在这里显示结果。</p></div>')
    elif state == "running" or "submitted_process" in st.session_state:
        html('<div class="empty-state processing-empty"><div class="empty-icon">◌</div><h3>正在把对话整理成文字。</h3><p>完成后会自动显示在这里。<br>等待时，可以先在「复盘笔记」记下这场面试的感受。</p></div>')
    else:
        html('<div class="empty-state"><div class="empty-icon">◉</div><h3>让对话成为可回看的记录。</h3><p>录屏已建档，开始一次本地转写，即可检索对话、回听和导出字幕。</p></div>')


with review_tab:
    st.caption("由 Codex 维护原存档目录中的 Markdown，Echo 只读取；点击刷新载入外部修改。")
    st.button("刷新复盘文档", key="refresh-review")
    document_path = reviews.review_path(item["path"])
    st.caption("本场文档：" + str(document_path))
    st.download_button("下载本场复盘模板", reviews.template(item, history), file_name=document_path.name, mime="text/markdown")
    with st.expander("交给 Codex 的复盘说明"):
        st.code(f"请按 {core.ROOT / 'docs/codex-review.md'} 的统一标准复盘。\n录屏：{item['path']}\ninterview_id：{item['id']}\n台账：{core.DATA / 'ledger.sqlite3'}\n输出：{document_path}\n个人背景：{core.DATA / 'profile.json'}（若存在，作为用户填写的参考资料，结合简历核对）\n先核对转写覆盖范围与版本；只追加复盘和更正，不覆盖原始笔记。", language=None)
    document = reviews.read_document(document_path, kind="interview", interview_id=item["id"])
    if document:
        for warning in document["warnings"]:
            st.warning(warning)
        meta = document["metadata"]
        st.caption(f"状态：{meta.get('status', '未标注')} · 更新：{meta.get('updated_at', '未标注')}")
        ids = meta.get("source_run_ids", [])
        if isinstance(ids, list) and any(isinstance(rid, str) and rid not in {r['id'] for r in history} for rid in ids):
            st.warning("复盘引用了当前档案中不存在的转写版本，请核对来源。")
        actions = reviews.action_items(document["body"])
        if actions:
            cols = st.columns(3)
            for col, action_status in zip(cols, ["待回听", "待练习", "已验证"]):
                col.metric(action_status, sum(a["状态"] == action_status for a in actions))
            with st.expander("复盘行动清单", expanded=True):
                st.dataframe(actions, hide_index=True, width="stretch")
        else:
            st.caption("尚未记录结构化行动；Codex 可按统一模板追加待回听、待练习和已验证项。")
        st.markdown(document["body"], unsafe_allow_html=False)
        if result and output and (output / "audio.mp3").is_file():
            times = sorted(set(re.findall(r"\b\d{2}:\d{2}:\d{2}\b", document["body"])))
            offset = result.get("source_start", 0)
            def seconds(value):
                h, m, sec = map(int, value.split(":"))
                return h * 3600 + m * 60 + sec
            playable = [t for t in times if offset <= seconds(t) < offset + result.get("duration", 0)]
            if playable:
                evidence = st.selectbox("复盘证据回听", playable, key=f"evidence-{run['id']}")
                if st.button("回听这段证据", key="review-play-" + run["id"]):
                    st.session_state["seek-" + run["id"]] = seconds(evidence)
                    st.session_state["seek-token-" + run["id"]] = st.session_state.get("seek-token-" + run["id"], 0) + 1
                    st.session_state["play-request-" + run["id"]] = True
            if len(playable) < len(times):
                st.caption("部分复盘时间不在当前转写版本覆盖范围内，请切换到对应版本回听。")
    else:
        st.info("本场尚无 Codex 复盘文档。可将上方说明交给 Codex，完成后刷新读取。")
    st.divider()
    st.markdown("### 跨场总结")
    summary_path = source_root / "面试总结.md"
    st.caption(str(summary_path))
    summary = reviews.read_document(summary_path, kind="summary")
    if summary:
        for warning in summary["warnings"]:
            st.warning(warning)
        st.markdown(summary["body"], unsafe_allow_html=False)
    else:
        st.caption("尚无跨场总结；Codex 可在存档根目录维护 面试总结.md。")

with core.db() as c:
    notes = [dict(r) for r in c.execute("SELECT at,body FROM notes WHERE interview_id=? ORDER BY seq DESC", (item["id"],))]
    events = [dict(r) for r in c.execute("SELECT at,kind,detail FROM events WHERE interview_id=? ORDER BY seq DESC", (item["id"],))]
with notes_tab:
    form_col, list_col = st.columns([1, 1.3], gap="large")
    with form_col:
        html('<div class="section-heading"><h3>写下这一次的收获</h3><span>REFLECTION</span></div>')
        with st.form(f"note-{selected_key}", clear_on_submit=True):
            body = st.text_area("复盘内容", placeholder="哪些问题值得重练？\n哪些术语需要纠正？\n下一次，我会怎样回答？", height=220)
            if st.form_submit_button("＋  追加记录", type="primary", width="stretch"):
                try:
                    core.add_note(item["id"], body)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        st.caption("每一次补充都会单独保存，不覆盖已有笔记。")
    with list_col:
        html(f'<div class="section-heading"><h3>复盘时间线</h3><span>{len(notes):02d} NOTES</span></div>')
        if not notes:
            st.info("还没有笔记。从一个值得改进的回答开始记录。")
        for note in notes:
            html(f'<div class="note-card"><div class="note-date">{local_time(note["at"])}</div><p>{escape(note["body"])}</p></div>')
with history_tab:
    html('<div class="section-heading"><h3>每一步，都有记录</h3><span>ACTIVITY</span></div>')
    labels = {"discovered": "录屏已建档", "source_changed": "源文件已更新", "started": "开始转写", "completed": "转写已完成", "failed": "转写失败", "interrupted": "转写已中断", "note_added": "追加复盘笔记", "queued": "加入转写队列", "queue_cancelled": "取消等待任务", "queue_failed": "队列任务失败", "queue_updated": "更新等待任务参数"}
    for event in events:
        html(f'<div class="activity-row"><span class="activity-dot"></span><b>{escape(labels.get(event["kind"], event["kind"]))}</b><time>{local_time(event["at"])}</time></div>')
    with st.expander("查看详细记录"):
        st.dataframe(events, hide_index=True, width="stretch")
    submitted = [j for j in queue_snapshot if j["interview_id"] == selected_key]
    if submitted:
        st.markdown("**队列提交记录**")
        names = {"queued": "等待中", "running": "处理中", "completed": "已完成", "cancelled": "已取消", "failed": "失败", "interrupted": "已中断"}
        st.dataframe([{"提交时间": local_time(j["created_at"]), "状态": names[j["state"]],
            "模型": model_name(json.loads(j["options"])["model"]), "说明": j["error"] or ""} for j in reversed(submitted)], hide_index=True, width="stretch")
if result and output and (output / "audio.mp3").is_file():
    with st.container(key="playback-dock"):
        render_player(output / "audio.mp3", key=run["id"],
                      seek=st.session_state.get("seek-" + run["id"], result.get("source_start", 0)) - result.get("source_start", 0),
                      offset=result.get("source_start", 0), token=st.session_state.get("seek-token-" + run["id"], 0),
                      autoplay=st.session_state.pop("play-request-" + run["id"], False))
html('<div class="footer-note">回声 / Echo<span>持续积累，认真回看。</span></div>')
