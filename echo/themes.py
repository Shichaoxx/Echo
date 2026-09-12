"""Six local workspace palettes and durable appearance preferences."""
import json
from pathlib import Path
import os
import tempfile

# background, panel, sidebar, ink, muted, border, accent, soft, on-accent
THEMES = {
    "glass": ("雾白 · Glass", "light", ["#f5f6fa", "#ffffff", "#e9ecf6", "#202331", "#6f7487", "#e0e4ed", "#5968ca", "#e9edff", "#ffffff"]),
    "sage": ("雾松 · Light", "light", ["#f6f7f3", "#ffffff", "#eaf0e7", "#203d34", "#647461", "#dce4d6", "#35684d", "#dfead9", "#ffffff"]),
    "sand": ("暖砂 · Light", "light", ["#faf6ef", "#fffdf8", "#eee6d9", "#473d31", "#7a6a55", "#e3d8c5", "#966337", "#f0e2cb", "#ffffff"]),
    "sky": ("晴空 · Light", "light", ["#f3f6fb", "#ffffff", "#e7edf7", "#253b59", "#5e7391", "#d9e2ef", "#386cb0", "#e0eafa", "#ffffff"]),
    "midnight": ("深海 · Dark", "dark", ["#101720", "#18222f", "#131d28", "#e2eaf6", "#a0b1c8", "#2b3a4d", "#8db9ed", "#233c56", "#102337"]),
    "forest": ("墨林 · Dark", "dark", ["#111b17", "#1a2821", "#14211b", "#e1ece3", "#a0b5a4", "#304839", "#a1c89d", "#2c4532", "#132a1b"]),
    "violet": ("暮紫 · Dark", "dark", ["#1a1722", "#252030", "#1e1a29", "#ede5f5", "#b5a6c8", "#42354f", "#c4a4ed", "#413051", "#28163e"]),
}


def load_theme(data: Path):
    try:
        value = json.loads((data / "preferences.json").read_text(encoding="utf-8")).get("theme")
        return value if value in THEMES else "sage"
    except (OSError, ValueError, AttributeError):
        return "sage"


def save_theme(data: Path, key: str):
    if key not in THEMES:
        raise ValueError("未知主题")
    data.mkdir(parents=True, exist_ok=True)
    path = data / "preferences.json"
    try:
        prefs = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(prefs, dict):
            prefs = {}
    except (OSError, ValueError):
        prefs = {}
    prefs["theme"] = key
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=data, delete=False) as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)
        temporary = f.name
    os.replace(temporary, path)


def theme_css(key: str):
    _, mode, colors = THEMES[key]
    names = ["paper", "panel", "sidebar", "ink", "muted", "line", "accent", "soft", "on-accent"]
    values = dict(zip(names, colors))
    values.update({"warning": "#e3bf7e" if mode == "dark" else "#866121",
                   "warning-bg": "#423623" if mode == "dark" else "#f4ead6",
                   "danger": "#f1a69a" if mode == "dark" else "#a04835",
                   "danger-bg": "#482b2d" if mode == "dark" else "#f8e3df"})
    declarations = ";".join(f"--{k}:{v}" for k, v in values.items())
    return f"<style>:root,.stApp{{{declarations};color-scheme:{mode}}}</style>"
