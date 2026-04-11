#!/usr/bin/env bash
# Dev-сервер с reload без обрыва долгих прогонов swarm (PM→…→Dev→…).
# Нужен пакет watchfiles (см. requirements.txt), иначе uvicorn использует StatReload
# и --reload-exclude не работает — любой save в tests/*.py убьёт текущий запрос.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
exec "$ROOT/.venv/bin/uvicorn" orchestrator_api:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload \
  --reload-exclude tests \
  --reload-exclude artifacts \
  --reload-exclude .venv \
  --reload-exclude .git \
  --reload-exclude .pytest_cache
