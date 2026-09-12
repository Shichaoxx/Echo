from datetime import datetime
import streamlit as st


def html(text):
    st.markdown(text, unsafe_allow_html=True)


def local_time(value):
    return datetime.fromisoformat(value).astimezone().strftime("%m.%d · %H:%M")


def open_queue():
    st.session_state["workspace_view"] = "全局队列"
