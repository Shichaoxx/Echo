"""Continuous client-side reading without Python reruns on search or playback."""
from pathlib import Path
import streamlit as st


def render_transcript(segments, key, offset=0):
    component = st.components.v2.component(
        "echo_transcript", isolate_styles=False,
        html='''<section class="echo-reader" aria-label="连续逐字稿">
          <div class="reader-tools"><input type="search" placeholder="搜索本场对话" aria-label="搜索本场对话">
          <button type="button" data-action="previous" aria-label="上一个匹配">↑</button>
          <button type="button" data-action="next" aria-label="下一个匹配">↓</button>
          <label><input type="checkbox" data-follow checked> 跟随播放</label></div>
          <div class="reader-status" aria-live="polite"></div>
          <div class="reader-scroll" tabindex="0" aria-label="逐字稿滚动区域"></div>
          <button class="reader-return" type="button">回到播放位置</button></section>''',
        css='''.echo-reader{color:var(--ink)}.reader-tools{display:flex;align-items:center;gap:6px;margin-bottom:12px}
        .reader-tools input[type=search]{min-width:80px;flex:1;border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--panel);color:var(--ink)}
        .reader-tools button,.reader-return{border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--muted);padding:7px 10px;cursor:pointer}
        .reader-tools label{white-space:nowrap;font-size:12px;color:var(--muted)}
        .reader-tools input[type=checkbox]{accent-color:var(--accent)}
        .reader-status{font-size:11px;color:var(--muted);margin:8px 0}
        .reader-scroll{height:clamp(340px,55vh,680px);overflow:auto;overscroll-behavior:contain;scrollbar-width:thin;scrollbar-color:var(--line) transparent}
        .reader-row{display:grid;grid-template-columns:78px 1fr;gap:14px;padding:20px 12px;border-left:3px solid transparent;border-radius:8px;margin:4px 0}
        .reader-row button{background:none;border:0;color:var(--muted);font:12px ui-monospace,monospace;text-align:left;cursor:pointer;align-self:start;padding:7px 0}
        .reader-row p{font-size:15px;line-height:1.9;margin:0;white-space:pre-wrap;overflow-wrap:anywhere}
        .reader-row.active{background:var(--soft);border-left-color:var(--accent)}.reader-row.active button{color:var(--accent)}
        .reader-row.match p{background:color-mix(in srgb,var(--accent) 10%,transparent)}
        .reader-row.current-match{outline:1px solid var(--accent);outline-offset:-1px}
        .reader-return{margin-top:12px}.reader-tools button:disabled{opacity:.35;cursor:default}
        @media(max-width:650px){.reader-tools{flex-wrap:wrap}.reader-row{gap:8px;grid-template-columns:68px 1fr}}''',
        js=(Path(__file__).resolve().parents[2] / "assets/transcript.js").read_text(),
    )
    component(data={"segments": segments, "player": "player-" + key, "offset": offset}, key="reader-" + key)
