"""Explicitly load bundled fictional examples; never replace an existing document."""

import json
from pathlib import Path

from .store import Problem


def seed(store, root):
    root = Path(root).resolve()
    for entry in reversed(json.loads((root / "catalog.json").read_text(encoding="utf-8"))):
        folder = (root / entry["id"]).resolve()
        if folder.parent != root:
            raise Problem("示例目录无效。")
        audio = folder / "audio.mp3"
        with audio.open("rb") as handle:
            recording = store.add_media(
                handle, audio.stat().st_size, audio.name, entry["title"], entry["date"]
            )
        key = recording["id"]
        if recording["latest_revision"]:
            continue
        source = json.loads((folder / "source.json").read_text(encoding="utf-8"))
        run = store.import_transcript(key, source, reuse=True)
        template = store.handoff(key, run["id"])["template"]
        spec = json.loads((folder / "document.json").read_text(encoding="utf-8"))
        template.update(spec)
        store.import_minutes(key, template)
