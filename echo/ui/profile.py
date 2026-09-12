"""User-authored context for Codex, stored locally and separate from transcripts."""
import streamlit as st
from echo import library


def render_profile(data, profile):
    st.title("个人 Profile")
    st.caption("维护你的求职背景，让每次复盘有一致的上下文。资料仅保存在本机。")
    with st.form("personal-profile"):
        left, right = st.columns(2)
        values = {}
        values["name"] = left.text_input("姓名 / 昵称", profile.get("name", ""), key="profile-name")
        values["role"] = right.text_input("目标岗位", profile.get("role", ""), key="profile-role")
        values["resume"] = st.text_input("简历版本 / 本地路径", profile.get("resume", ""), placeholder="例如：秋招简历 v3；也可填写本地文件路径", key="profile-resume")
        values["background"] = st.text_area("项目与实习背景", profile.get("background", ""), height=180,
                                          placeholder="业务背景、你的职责、已确认的结果；团队能力与个人工作分开填写。", key="profile-background")
        values["focus"] = st.text_area("当前练习重点", profile.get("focus", ""), height=100, key="profile-focus")
        if st.form_submit_button("保存个人资料", type="primary"):
            try:
                library.save_profile(data, values)
                st.session_state["profile-saved"] = True
                st.rerun()
            except (OSError, ValueError) as exc:
                st.error(f"未能保存，请保留输入并重试：{exc}")
    if st.session_state.pop("profile-saved", False):
        st.success("个人资料已保存。")
    st.download_button("导出给 Codex 的背景资料", library.profile_context(profile), file_name="个人Profile.md", mime="text/markdown")
    with st.expander("复盘时如何使用"):
        st.write("本场复盘说明会附带 Profile 路径。Codex 可读取后结合简历和面试材料核对；Echo 不自动生成复盘。简历路径是资料引用，不会自动上传文件。")
        st.caption(str(data / "profile.json"))
