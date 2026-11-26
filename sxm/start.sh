#!/usr/bin/env bash
# macOS/Linux: first run creates the venv, then starts the dashboard + scheduler.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
[ -f .env ] || cp .env.example .env
exec .venv/bin/python -m sxm
