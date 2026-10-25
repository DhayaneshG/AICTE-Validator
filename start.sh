#!/usr/bin/env bash
# Railway/production start: seeds the demo data once into the persistent volume, then serves on $PORT.
set -e
export AICTE_VAR="${AICTE_VAR:-/data}"
mkdir -p "$AICTE_VAR"
if [ ! -f "$AICTE_VAR/.seeded" ]; then
  echo "First start: seeding synthetic demo data (about 90 s)..."
  python -m backend.app.seed
  touch "$AICTE_VAR/.seeded"
fi
exec uvicorn backend.app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
