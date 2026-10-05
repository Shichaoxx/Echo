#!/bin/zsh
set -eu
cd -- "$(dirname -- "$0")"
echo_python="${ECHO_PYTHON:-.venv/bin/python}"
if [[ ! -x "$echo_python" ]]; then
  print -u2 '未找到 Echo Python 环境。请先创建 .venv 并安装 requirements.txt，或设置 ECHO_PYTHON。'
  exit 1
fi
exec "$echo_python" workbench.py --port "${ECHO_PORT:-18770}" "$@"
