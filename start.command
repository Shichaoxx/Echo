#!/bin/zsh
set -eu
cd -- "${0:A:h}"

library_dir="${ECHO_DATA_DIR:-$HOME/Library/Application Support/Echo 1.x/library}"
listen_port=18771
server_options=()
if [[ "${1:-}" == "--demo" ]]; then
  shift
  library_dir="${ECHO_DEMO_DIR:-$HOME/Library/Application Support/Echo 1.x/demo}"
  listen_port=18772
  server_options+=(--seed-examples)
fi

if ! command -v npm >/dev/null || ! command -v ffprobe >/dev/null; then
  print -u2 '请先安装 Node.js（含 npm）以及 FFmpeg（含 ffprobe）。'
  exit 1
fi

if [[ ! -x frontend/node_modules/.bin/vite || frontend/package-lock.json -nt frontend/node_modules/.package-lock.json ]]; then
  npm --prefix frontend ci --no-fund --no-audit
fi
npm --prefix frontend run build

interpreter="${ECHO_PYTHON:-python3}"
if [[ -z "${ECHO_PYTHON:-}" && -x .venv/bin/python ]]; then
  interpreter=.venv/bin/python
fi
# An existing local model can serve both generations without opening an old library.
if [[ -z "${ECHO_WHISPER_MODEL:-}" && -f "$HOME/Library/Application Support/Echo/model-turbo/config.json" ]]; then
  server_options+=(--model-dir "$HOME/Library/Application Support/Echo/model-turbo")
fi
exec "$interpreter" -m server --data-dir "$library_dir" --port "$listen_port" "${server_options[@]}" "$@"
