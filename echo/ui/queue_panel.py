"""Live task progress and dedicated queue management."""
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import json
import streamlit as st
from echo import interview as core, queue_jobs as queue
from echo.asr_models import MODELS, LANGUAGES, model_name
from echo.task_status import duration_label, elapsed_seconds, estimate_remaining
from echo.transcription_ui import scope_label, range_error, media_duration
from echo.ui.common import html, local_time, open_queue


@st.fragment(run_every="3s")
def show_progress(global_view, *, queue_snapshot, run_snapshot, worker_busy, lookup, state, selected_key):
    queued = queue.jobs()
    if [(j["id"], j["state"], j["options"]) for j in queued] != [(j["id"], j["state"], j["options"]) for j in queue_snapshot]:
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
    if global_view:
        st.markdown("# 全局转写队列")
        st.caption("所有面试共用 · 每 3 秒刷新 · 正在执行的任务参数固定，等待任务可修改")
        cols = st.columns(4)
        for col, label, count in zip(cols, ["执行中", "等待中", "队列已完成", "需处理"],
            [int(active), sum(j["state"] == "queued" for j in queued),
             sum(j["state"] == "completed" for j in queued), sum(j["state"] in ("failed", "interrupted") for j in queued)]):
            col.metric(label, count)
    running = next((r for r in live if r["state"] == "running" and (global_view or r["interview_id"] == selected_key)), None) if active else None
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
    elif not global_view and state == "interrupted":
        st.warning("转写进程已停止，尚未生成完整结果。可展开下方设置重新转写，旧版本仍保留。")
    elif not global_view and state == "completed":
        html('<div class="ready-strip"><span>✓ 最新转写已就绪</span><span>逐字稿、回听与导出已可用</span></div>')
    if global_view:
        if not active and not any(j["state"] == "queued" for j in queued):
            st.caption("当前没有活动任务，可以选择面试提交转写。")
        with st.expander("全局任务记录", expanded=False):
            if queued:
                names = {"queued": "等待中", "running": "处理中", "completed": "已完成", "cancelled": "已取消", "failed": "失败", "interrupted": "已中断"}
                st.dataframe([{"面试": lookup.get(j["interview_id"], {}).get("title", j["interview_id"]),
                    "状态": names[j["state"]], "模型": model_name(json.loads(j["options"])["model"]),
                    "范围": scope_label(json.loads(j["options"])), "提交时间": local_time(j["created_at"]),
                    "说明": j["error"] or ""} for j in reversed(queued)], hide_index=True, width="stretch")
            else:
                st.caption("尚无队列提交记录。直接启动的转写仍显示在上方执行状态中。")
    waiting = [j for j in queued if j["state"] == "queued"]
    if not global_view:
        own_waiting = [j for j in waiting if j["interview_id"] == selected_key]
        if own_waiting:
            first_position = next(n for n, j in enumerate(waiting, 1) if j["interview_id"] == selected_key)
            st.info(f"本场有 {len(own_waiting)} 项等待任务 · 最早排在第 {first_position} 位")
            st.button("管理等待任务 →", on_click=open_queue, key="manage-own-queue")
        return
    if waiting:
        with st.expander(f"等待队列 · {len(waiting)} 场", expanded=True):
            st.caption("当前转写结束后自动继续。取消仅移除等待任务，已有结果不受影响。")
            for position, job in enumerate(waiting, 1):
                details, action = st.columns([5, 1])
                opts = json.loads(job["options"])
                label = lookup.get(job["interview_id"], {}).get("title", "面试").split(" / ")[0]
                details.markdown(f"**{position:02} · {escape(label)}**")
                details.caption(model_name(opts["model"]) + " · " + scope_label(opts))
                if action.button("取消", key="cancel-" + job["id"]):
                    queue.cancel(job["id"])
                    st.rerun()
                with st.expander("修改等待参数 · " + label):
                    with st.form("edit-" + job["id"]):
                        model = st.selectbox("等待任务模型", list(MODELS), index=list(MODELS).index(opts["model"]), format_func=model_name, key="qm-" + job["id"])
                        lang = st.selectbox("等待任务语言", list(LANGUAGES), index=list(LANGUAGES).index(opts["language"]), format_func=lambda x: LANGUAGES[x], key="ql-" + job["id"])
                        qs = st.number_input("开始秒数", min_value=0, value=int(opts.get("start", 0)), key="qs-" + job["id"])
                        ql = st.number_input("转写秒数（0 表示至结束）", min_value=0, value=int(opts.get("limit", 0)), key="qn-" + job["id"])
                        qc = st.slider("Qwen 分块秒数", 10, 60, int(opts.get("chunk_seconds", 25)), key="qc-" + job["id"])
                        qf = st.checkbox("Qwen 静音过滤", value=opts.get("silence_filter", True), key="qf-" + job["id"])
                        qp = st.text_area("等待任务术语提示", value=opts.get("prompt", core.PROMPT), key="qp-" + job["id"])
                        force_edit = st.checkbox("强制生成新版本", value=opts.get("force", False), key="qforce-" + job["id"])
                        st.caption("分块与静音过滤仅对 Qwen 生效；保存后保留排队位置，并记录参数修改历史。")
                        if st.form_submit_button("保存等待参数"):
                            try:
                                queued_item = lookup[job["interview_id"]]
                                stat = Path(queued_item["path"]).stat()
                                duration = media_duration(queued_item["path"], stat.st_size, stat.st_mtime_ns)
                                error = range_error(qs, ql, duration)
                                if error:
                                    raise ValueError(error)
                                queue.revise(job["id"], dict(model=model, language=lang, start=qs, limit=ql,
                                    chunk_seconds=qc, silence_filter=qf, prompt=qp, force=force_edit))
                                st.rerun()
                            except (ValueError, OSError) as exc:
                                st.error(str(exc))

