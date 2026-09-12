#!/bin/zsh
set -eu
cd -- "$(dirname -- "$0")/.."
exec .venv/bin/python scripts/archive_interviews.py
