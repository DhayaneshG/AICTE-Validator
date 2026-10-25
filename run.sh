#!/usr/bin/env bash
# Start the app: creates venv if missing, builds the frontend if missing, serves on :8000
set -e
cd "$(dirname "$0")"
if [ ! -d venv ]; then python3.11 -m venv venv 2>/dev/null || python3 -m venv venv; fi
. venv/bin/activate
pip install -q -r requirements.txt
if [ ! -d frontend/dist ]; then (cd frontend && npm install --no-audit --no-fund && npm run build); fi
exec uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
