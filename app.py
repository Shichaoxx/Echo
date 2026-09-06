"""Local interview workspace. Start through start.command."""
from datetime import datetime, timezone
from html import escape
import json
import math
from pathlib import Path
import re
import subprocess
import sys

import streamlit as st
import interview as core
import queue_jobs as queue
from asr_models import MODELS, LANGUAGES, model_name, model_ready, load_default_model, save_default_model
from themes import THEMES, load_theme, save_theme, theme_css
from task_status import elapsed_seconds, duration_label, estimate_remaining

st.set_page_config(page_title="回声 · Echo", page_icon="◉", layout="wide")
if "appearance" not in st.session_state:
    st.session_state["appearance"] = load_theme(core.DATA)
st.markdown(theme_css(st.session_state["appearance"]), unsafe_allow_html=True)
st.markdown((core.ROOT / "style.css").read_text(encoding="utf-8"), unsafe_allow_html=True)


def html(text):
    st.markdown(text, unsafe_allow_html=True)


def local_time(value):
    return datetime.fromisoformat(value).astimezone().strftime("%m.%d · %H:%M")


def highlighted(text, query):
    if not query:
        return escape(text)
    return "".join(f"<mark>{escape(part)}</mark>" if i % 2 else escape(part)
                   for i, part in enumerate(re.split(f"({re.escape(query)})", text, flags=re.I)))


items = core.interviews()
lookup = {i["id"]: i for i in items}
all_runs = {i["id"]: core.runs(i["id"]) for i in items}
worker_busy = core.worker_active()
run_snapshot = {(r["id"], r["state"]) for rs in all_runs.values() for r in rs}
queue_snapshot = queue.jobs()
if any(j["state"] == "queued" for j in queue_snapshot):
    queue.ensure_dispatcher()
finished = sum(any(r["state"] == "completed" for r in rs) for rs in all_runs.values())
default_key = next((i["id"] for i in items if any(r["state"] == "completed" for r in all_runs[i["id"]])), next(iter(lookup), None))

with st.sidebar:
    html('<div class="brand"><span class="brand-icon">◉</span><div>回声<span class="brand-en">Echo</span></div></div>')
    st.selectbox("界面主题", list(THEMES), format_func=lambda key: THEMES[key][0],
                 key="appearance", on_change=lambda: save_theme(core.DATA, st.session_state["appearance"]))
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
    html('<div class="side-label">你的面试资料库</div>')
    html(f'<div class="library-count"><b>{len(items):02d}</b><span>场面试 · {finished} 场已转写</span></div>')
    if items:
        selected_key = st.radio("面试档案", list(lookup), index=list(lookup).index(default_key),
            format_func=lambda key: ("◌  " if worker_busy and any(r["state"] == "running" for r in all_runs[key]) else "◉  " if any(r["state"] == "completed" for r in all_runs[key]) else "○  ") + lookup[key]["title"].split(" / ")[0],
            label_visibility="collapsed", key="selected_interview")
    st.divider()
    with st.expander("导入与目录设置"):
        source = st.text_input("面试目录", str(core.SOURCE))
        if st.button("扫描新增录屏", type="primary", width="stretch"):
            try:
                with st.spinner("正在核对录屏…"):
                    counts = core.scan(Path(source))
                st.session_state["scan_message"] = f"新增 {counts['added']} 场 · 更新 {counts['changed']} 场 · 等待稳定 {counts['deferred']} 场"
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        st.caption("新增录屏独立建档，已有版本与笔记始终保留。")
    if "scan_message" in st.session_state:
        st.success(st.session_state.pop("scan_message"))
    st.button("↻  刷新资料库", width="stretch")
    html('<div class="local-card"><span class="status-dot"></span> 本地工作空间<p>录屏与个人笔记保存在这台 Mac<br>多模型 · 本地转写</p></div>')

if not items:
    html('<div class="empty-state"><div class="eyebrow">YOUR FIRST INTERVIEW</div><h1>从第一场面试开始。</h1><p>在左侧选择录屏目录，将每一次对话、思考和复盘收录在一起。</p></div>')
    st.stop()

item = lookup[selected_key]
history = all_runs[selected_key]
completed = [r for r in history if r["state"] == "completed"]
state = history[0]["state"] if history else "pending"
if state == "running" and not worker_busy:
    state = "interrupted"
if state != "running" and any(j["state"] == "queued" and j["interview_id"] == selected_key for j in queue_snapshot):
    state = "queued"
state_name = {"completed": "已完成转写", "running": "正在转写", "queued": "已排队", "failed": "等待重试", "interrupted": "转写已中断", "pending": "待转写"}[state]
short_title = item["title"].split(" / ")[0]
parts = re.split(r"\s*——\s*", short_title, maxsplit=1)
date_label, title = (parts[0], parts[1]) if len(parts) == 2 else ("面试档案", short_title)
html(f'<div class="workspace-top"><span>工作空间 <span class="slash">/</span> 面试档案</span><span class="private-tag">● &nbsp;仅本机可见</span></div>')
html(f'<div class="hero"><div><div class="eyebrow">INTERVIEW / {escape(date_label)}</div><h1>{escape(title)}</h1><p>重听对话，梳理思路，让每一次面试都有迹可循。</p><span class="status-pill {state}">{escape(state_name)}</span></div><div class="sound-art" aria-hidden="true">' + ''.join(f'<i style="height:{h}px"></i>' for h in [18,32,54,28,76,104,66,130,92,152,110,64,94,130,74,42,68,32,20]) + '</div></div>')


@st.fragment(run_every="3s")
def show_progress():
    queued = queue.jobs()
    if [(j["id"], j["state"]) for j in queued] != [(j["id"], j["state"]) for j in queue_snapshot]:
        st.rerun()
    with core.db() as c:
        live = [dict(r) for r in c.execute("SELECT * FROM runs ORDER BY started_at DESC")]
        progress_rows = {r["run_id"]: dict(r) for r in c.execute("SELECT * FROM progress")}
    current = {(r["id"], r["state"]) for r in live}
    active = core.worker_active()
    pending = st.session_state.get("submitted_process")
    if pending is not None and pending.poll() is not None:
        code = pending.returncode
        del st.session_state["submitted_process"]
        st.session_state["submission_notice"] = "任务已处理，结果已更新；相同参数的已有版本会直接复用。" if code == 0 else "任务未完成，请查看处理记录；启动错误详见本机 .local/worker.log。"
        st.rerun()
    if current != run_snapshot or active != worker_busy:
        for r in live:
            if r["state"] == "completed" and (r["id"], "completed") not in run_snapshot:
                st.session_state.pop(f"version-{r['interview_id']}", None)
        st.rerun()
    running = next((r for r in live if r["state"] == "running"), None) if active else None
    if running:
        row = progress_rows.get(running["id"], {})
        stage = row.get("stage", "准备开始")
        done, total = row.get("done", 0), row.get("total", 0)
        now = datetime.now(timezone.utc)
        elapsed = duration_label(elapsed_seconds(running["started_at"], now))
        anchor_key = "pace-" + running["id"]
        if total > 1 and anchor_key not in st.session_state:
            st.session_state[anchor_key] = (done, now.timestamp())
        remaining = estimate_remaining(st.session_state.get(anchor_key), done, total, now.timestamp()) if total > 1 else None
        eta = "预计剩余 " + duration_label(remaining) + " · 估算" if remaining is not None else "正在估算剩余时间" if total > 1 and done < total else "正在处理，请稍候"
        phase = 0 if "提取" in stage else 1 if "模型" in stage or "准备" in stage else 3 if "保存" in stage or "完成" in stage else 2
        steps = ''.join(f'<span class="{"step-current" if n == phase else "step-done" if n < phase else ""}">{"✓" if n < phase else str(n + 1).zfill(2)} {label}</span>' for n, label in enumerate(["提取音轨", "加载模型", "识别对话", "保存结果"]))
        title = lookup.get(running["interview_id"], {}).get("title", "面试转写").split(" / ")[0]
        percent = f"{done / total:.0%}" if total > 1 else "处理中"
        meter = f'<div class="task-fill" style="width:{min(100, done / total * 100):.1f}%"></div>' if total > 1 else '<div class="task-sweep"></div>'
        detail = f"已处理 {done} / {total} 个音频块" if total > 1 else stage
        html(f'<section class="task-card" aria-label="当前转写任务"><div class="task-heading"><div><div class="eyebrow"><span class="pulse-dot"></span> 正在本机处理</div><h3>{escape(title)}</h3><p>{escape(model_name(running["model"]))}</p></div><strong>{percent}</strong></div><div class="task-steps">{steps}</div><div class="task-track" role="progressbar" aria-label="音频块处理进度" {f"aria-valuenow={done} aria-valuemax={total}" if total > 1 else ""}>{meter}</div><div class="task-meta"><span>{escape(detail)}</span><span>已用 {elapsed} · {eta}</span></div><div class="task-foot">完成后自动更新结果 · 可以切换面试查看已有记录</div></section>')
        if elapsed_seconds(row.get("updated_at", running["started_at"]), now) > 120:
            st.caption("当前步骤已有一段时间未更新；模型加载或长音频处理可能需要较久，暂不能据此判断失败。")
    elif pending is not None:
        st.info("正在启动本地转写任务…")
    elif state == "interrupted":
        st.warning("转写进程已停止，尚未生成完整结果。可展开下方设置重新转写，旧版本仍保留。")
    elif state == "completed":
        html('<div class="ready-strip"><span>✓ 最新转写已就绪</span><span>逐字稿、回听与导出已可用</span></div>')
    waiting = [j for j in queued if j["state"] == "queued"]
    if waiting:
        with st.expander(f"等待队列 · {len(waiting)} 场", expanded=True):
            st.caption("当前转写结束后自动继续。取消仅移除等待任务，已有结果不受影响。")
            for position, job in enumerate(waiting, 1):
                details, action = st.columns([5, 1])
                opts = json.loads(job["options"])
                label = lookup.get(job["interview_id"], {}).get("title", "面试").split(" / ")[0]
                details.markdown(f"**{position:02} · {escape(label)}**")
                details.caption(model_name(opts["model"]) + " · " + (f"{opts['start']} 秒起 / {opts['limit']} 秒片段" if opts.get("limit") else "完整录屏"))
                if action.button("取消", key="cancel-" + job["id"]):
                    queue.cancel(job["id"])
                    st.rerun()


show_progress()
if "submission_notice" in st.session_state:
    st.toast(st.session_state.pop("submission_notice"))

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

with st.expander("新建转写 / 更换模型重转", expanded=not history):
    spec = MODELS[selected_model]
    st.markdown("**" + spec.name + "**")
    st.caption(spec.timing + " · " + ("模型已就绪" if model_ready(selected_model, core.DATA) else spec.description))
    language = st.selectbox("录音语言", list(LANGUAGES), format_func=lambda x: LANGUAGES[x], key=f"language-{selected_key}")
    mode = st.radio("转写范围", ["完整录屏", "指定片段试转"], horizontal=True, key=f"mode-{selected_key}")
    start, limit = 0, 0
    if mode == "指定片段试转":
        start = st.number_input("起始位置（秒）", min_value=0, value=300, step=30, key=f"start-{selected_key}")
        limit = st.number_input("片段长度（秒）", min_value=1, max_value=3600, value=180, step=30, key=f"limit-{selected_key}")
        st.caption("建议选有实际对话的技术讨论片段；例如从 300 秒开始，避开开头候场。")
    prompt = st.text_area("技术术语 / 上下文提示", core.PROMPT, key=f"prompt-v2-{selected_key}")
    chunk_seconds, silence_filter = 25, True
    if spec.backend == "qwen":
        chunk_seconds = st.slider("音频分段时长（秒）", min_value=10, max_value=60, value=25, step=5,
            help="每次交给模型的音频长度上限，整场录音都会处理。较短方便定位，较长保留更多上下文；建议 20–25 秒。")
        silence_filter = st.checkbox("跳过近乎静音的音频块", value=True)
        st.caption("过滤阈值为 −55 dBFS；特别轻的声音可能被跳过，可关闭。")
    force = st.checkbox("强制生成新版本，保留已有结果", key=f"force-{selected_key}")
    busy = worker_busy or "submitted_process" in st.session_state or any(j["state"] in ("queued", "running") for j in queue_snapshot)
    if st.button("加入转写队列" if queue_mode else "开始本地转写", type="primary", disabled=busy and not queue_mode):
        if queue_mode:
            queue.submit(item["id"], dict(model=selected_model, language=language, start=start, limit=limit,
                chunk_seconds=chunk_seconds, silence_filter=silence_filter, prompt=prompt, force=force))
            queue.ensure_dispatcher()
            st.session_state["submission_notice"] = "已加入队列，将按顺序处理。相同参数的等待任务不会重复添加。"
            st.rerun()
        core.DATA.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(core.ROOT / "interview.py"), "transcribe", item["id"],
            "--model", selected_model, "--language", language, "--start", str(start), "--limit", str(limit),
            "--chunk-seconds", str(chunk_seconds), "--prompt", prompt]
        if not silence_filter:
            command.append("--no-silence-filter")
        if force:
            command.append("--force")
        with (core.DATA / "worker.log").open("ab") as log:
            st.session_state["submitted_process"] = subprocess.Popen(command, cwd=core.ROOT, stdout=log, stderr=log, start_new_session=True)
        st.rerun()
    if busy and not queue_mode:
        st.caption("已有任务正在运行，完成后可以提交新任务。")


segments_all = result.get("segments", []) if result else []
flag_count = sum(bool(core.review_reasons(s)) for s in segments_all)
duration = result.get("duration", max((s["end"] for s in segments_all), default=0)) if result else 0
metrics = [("录音时长", f"{duration / 60:.1f}" if result else "—", "分钟"), ("转写片段", f"{len(segments_all):,}", "段"), ("待核对", str(flag_count), "处提示"), ("历史版本", str(len(history)), "份记录")]
html('<div class="stat-grid">' + ''.join(f'<div class="stat-card"><span>{label}</span><div>{value}<small>{unit}</small></div></div>' for label, value, unit in metrics) + '</div>')

transcript_tab, notes_tab, history_tab = st.tabs(["逐字稿与回听", "复盘笔记", "处理记录"])
with transcript_tab:
    if result:
        if result.get("timestamp_kind") == "audio_chunk":
            st.info("Qwen 时间戳表示音频块范围，用于定位回听；不代表逐句对齐。")
            st.caption("Qwen 不提供 Whisper 的识别置信度指标，待核对数量不能用于跨模型评比。")
        if result.get("source_start", 0):
            st.caption(f"当前为指定片段：{core.stamp(result['source_start'])[:8]} 起；显示原录屏时间戳。")
        reading, listening = st.columns([1.9, 1], gap="large")
        with reading:
            html('<div class="section-heading"><h3>对话记录</h3><span>TRANSCRIPT</span></div>')
            query = st.text_input("搜索对话", placeholder="搜索关键词、技术术语或面试问题…", label_visibility="collapsed", key=f"query-{selected_key}")
            only_flags = st.toggle("只看待核对片段", key=f"flags-{selected_key}")
            segments = [s for s in segments_all if query.casefold() in s["text"].casefold() and (not only_flags or core.review_reasons(s))]
            page_size = 16
            pages = max(1, math.ceil(len(segments) / page_size))
            page = st.selectbox("阅读页码", range(1, pages + 1), format_func=lambda n: f"第 {n} / {pages} 页", key=f"page-{run['id']}-{query}-{only_flags}")
            page_segments = segments[(page - 1) * page_size:page * page_size]
            st.caption(f"共 {len(segments):,} 个匹配片段 · 时间戳对应原始录音")
            rows = []
            for s in page_segments:
                reasons = core.review_reasons(s)
                badge = f'<div class="review-tag">{escape(" · ".join(reasons))}</div>' if reasons else ""
                rows.append(f'<div class="transcript-row"><span class="timecode">{core.stamp(s["start"])[:8]}</span><div><p>{highlighted(s["text"].strip(), query)}</p>{badge}</div></div>')
            if rows:
                html('<div class="transcript-sheet">' + ''.join(rows) + '</div>')
            else:
                st.info("没有匹配的片段，试试其他关键词。")
        with listening:
            html('<div class="section-heading"><h3>回到现场</h3><span>LISTEN</span></div>')
            with st.container(border=True):
                html('<div class="player-label">AUDIO REVIEW</div><h4 class="player-title">听清每一个细节。</h4>')
                if segments and (output / "audio.mp3").is_file():
                    chosen = st.selectbox("选择片段回听", segments, format_func=lambda s: core.stamp(s["start"])[:8] + " · " + s["text"][:45], key=f"audio-{run['id']}-{query}-{only_flags}")
                    st.audio(str(output / "audio.mp3"), start_time=max(0, int(chosen["start"] - result.get("source_start", 0))))
                st.caption("选择时间戳，跳转到对应对话。")
            with st.container(border=True):
                html(f'<div class="review-heading"><span class="review-symbol">!</span><b>{flag_count} 处值得再听一次</b></div><p class="helper-copy">低音量、重复文本或识别置信度偏低的片段已标记。提示不代表准确率评分。</p>')
                st.caption("技术术语建议结合原声核对，纠正内容可追加到复盘笔记。")
            html('<div class="section-heading"><h3>导出这一场</h3><span>EXPORT</span></div>')
            for col, (label, filename) in zip(st.columns(2) * 2, [("↓  核对稿", "review.md"), ("↓  纯文本", "transcript.txt"), ("↓  SRT 字幕", "transcript.srt"), ("↓  JSON 数据", "transcript.json")]):
                col.download_button(label, (output / filename).read_bytes(), file_name=filename, key=filename, width="stretch")
            st.caption("当前支持音轨转写，暂不区分说话人或识别屏幕代码。")
    elif state == "queued":
        html('<div class="empty-state processing-empty"><div class="empty-icon">◷</div><h3>已排队，等待开始。</h3><p>轮到这场时会自动转写，完成后在这里显示结果。</p></div>')
    elif state == "running" or "submitted_process" in st.session_state:
        html('<div class="empty-state processing-empty"><div class="empty-icon">◌</div><h3>正在把对话整理成文字。</h3><p>完成后会自动显示在这里。<br>等待时，可以先在「复盘笔记」记下这场面试的感受。</p></div>')
    else:
        html('<div class="empty-state"><div class="empty-icon">◉</div><h3>让对话成为可回看的记录。</h3><p>录屏已建档，开始一次本地转写，即可检索对话、回听和导出字幕。</p></div>')

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
    labels = {"discovered": "录屏已建档", "source_changed": "源文件已更新", "started": "开始转写", "completed": "转写已完成", "failed": "转写失败", "interrupted": "转写已中断", "note_added": "追加复盘笔记", "queued": "加入转写队列", "queue_cancelled": "取消等待任务", "queue_failed": "队列任务失败"}
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
html('<div class="footer-note">回声 / Echo<span>持续积累，认真回看。</span></div>')
