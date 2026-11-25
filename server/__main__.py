"""Start a local Echo workspace and serve the built standalone reader."""

import argparse
import json
import os
import sys
import threading
from pathlib import Path

from . import asr
from .http import Server
from .seed import seed
from .store import Store


def monitor_launcher(server):
    # Finder starts the service with a pipe owned by the app. EOF also covers a force quit.
    while sys.stdin.buffer.read(4096):
        pass
    server.shutdown()


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18771)
    parser.add_argument("--ui-dir", type=Path, default=root / "frontend/dist")
    parser.add_argument("--model-dir", type=Path, default=os.environ.get("ECHO_WHISPER_MODEL"))
    parser.add_argument("--exit-on-stdin-eof", action="store_true", help="Stop when the desktop app exits.")
    parser.add_argument(
        "--seed-examples",
        action="store_true",
        help="Import the bundled fictional examples into this workspace.",
    )
    args = parser.parse_args()
    store = Store(args.data_dir)
    if args.seed_examples:
        seed(store, root / "examples")
    with store.db() as db:
        pending = db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone()
    with Server(store, args.port, args.ui_dir, args.model_dir) as server:
        if pending:
            asr.launch(store, args.model_dir)
        if args.exit_on_stdin_eof:
            threading.Thread(target=monitor_launcher, args=(server,), daemon=True).start()
        print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/"}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
