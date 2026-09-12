"""Stable Streamlit entry point. Implementation lives in echo/workspace.py."""
from pathlib import Path

# Execute on every Streamlit rerun; a normal import would cache the interface.
workspace = Path(__file__).parent / "echo/workspace.py"
exec(compile(workspace.read_text(encoding="utf-8"), str(workspace), "exec"))
