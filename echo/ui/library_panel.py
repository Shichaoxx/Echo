"""Grouped navigation over stable interview IDs, with durable view preferences."""
from html import escape
import streamlit as st
from echo import library
from echo.ui.common import html


def select_interview(key):
    st.session_state["selected_interview"] = key
    st.session_state["workspace_view"] = "面试详情"


def render_navigation(data, items, store, profile, status):
    html('<div class="echo-wordmark">echo<span>面试工作空间</span></div>')
    st.radio("工作区", ["面试详情", "全局队列", "个人 Profile"], key="workspace_view",
             format_func=lambda value: {"面试详情": "资料库", "全局队列": "转写队列", "个人 Profile": "个人 Profile"}[value],
             label_visibility="collapsed")
    search = st.text_input("搜索面试", placeholder="搜索公司、日期、轮次或标签", key="interview-search")
    if "library-view" not in st.session_state:
        st.session_state["library-view"] = store.get("view", "时间")
    st.radio("整理方式", ["时间", "公司"], key="library-view", horizontal=True,
             on_change=lambda: library.save_view(data, st.session_state["library-view"]))
    scope = st.selectbox("档案范围", ["进行中的面试", "已归档", "全部档案"], key="library-scope", label_visibility="collapsed")
    visible = []
    for item in items:
        meta = library.metadata(item, store)
        if scope == "进行中的面试" and meta["archived"] or scope == "已归档" and not meta["archived"]:
            continue
        if search.casefold() in " ".join(str(v) for v in (item["title"], meta["company"], meta["round"], meta["date"], meta["tags"])).casefold():
            visible.append(item)
    groups = library.group_items(visible, store, st.session_state["library-view"])
    keys = [item["id"] for group in groups.values() for item in group]
    if keys and st.session_state.get("selected_interview") not in keys:
        st.session_state["selected_interview"] = keys[0]
    selected = st.session_state.get("selected_interview") if keys else None
    st.caption(f"{len(visible)} 场面试 · {len(items)} 场收录")
    with st.container(height=360, border=False, key="library-tree"):
        for name, group in groups.items():
            with st.expander(f"{name} · {len(group)}", expanded=any(i["id"] == selected for i in group)):
                for item in group:
                    meta = library.metadata(item, store)
                    label = f"{meta['date'] or '日期未标注'} · {meta['company']} · {meta['round']}"
                    if meta['company'] == '未分类' or meta['round'] == '轮次未标注':
                        label = item['title'].split(' / ')[0]
                    st.button(label, key="nav-" + item["id"], width="stretch",
                              type="primary" if item["id"] == selected else "tertiary",
                              help=status(item["id"]), on_click=select_interview, args=(item["id"],))
        if not visible:
            st.info("没有匹配的面试，清空搜索或切换档案范围。")
    name = profile.get("name") or "我的 Profile"
    html(f'<div class="profile-identity"><span>{escape(name[:1])}</span><div>{escape(name)}<small>本地工作空间</small></div></div>')
    return selected


def render_archive_settings(data, item, store):
    meta = library.metadata(item, store)
    with st.expander("整理档案 · 公司、日期与标签"):
        st.caption("调整资料库中的分类与展示，不移动原始文件。")
        with st.form("archive-meta-" + item["id"]):
            a, b = st.columns(2)
            company = a.text_input("公司", meta["company"])
            round_name = b.text_input("轮次", meta["round"])
            day = a.text_input("面试日期", meta["date"], help="YYYY-MM-DD；年份未知用 MM-DD。")
            tags = b.text_input("标签", meta["tags"], placeholder="用逗号分隔")
            if st.form_submit_button("保存档案信息"):
                try:
                    library.update_metadata(data, item["id"], company=company.strip() or "未分类",
                                            round=round_name.strip() or "轮次未标注", date=day, tags=tags.strip())
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        if st.button("恢复到资料库" if meta["archived"] else "归档这场面试", key="archive-toggle-" + item["id"]):
            library.update_metadata(data, item["id"], archived=not meta["archived"])
            st.rerun()
