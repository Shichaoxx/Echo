#!/bin/zsh
set -eu
cd -- "$(dirname -- "$0")/.."
exec .venv/bin/python interview.py watch --interval 60
