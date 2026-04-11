# AIlourOS - local commands
# Usage: make help

SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV          ?= .venv
FRONTEND_DIR  := frontend
FRONTEND_LOCK := $(FRONTEND_DIR)/package-lock.json
FRONTEND_NPM_STAMP := $(FRONTEND_DIR)/node_modules/.package-lock.json
PY            := $(VENV)/bin/python
PIP           := $(VENV)/bin/pip
UVICORN       := $(VENV)/bin/uvicorn
LINT_IMPORTS  := $(VENV)/bin/lint-imports
COMPOSE_PS_FORMAT := table {{.Name}}\t{{.Status}}\t{{.Ports}}
export PYTHONPATH := $(CURDIR)

.PHONY: help venv install lint test test-security ci \
	pipeline frontend-install frontend-build frontend-lint e2e \
	logs ps restart models submodules \
	run start stop quickstart \
	_install-python _ensure-venv _frontend-ensure-deps _compose-up _compose-down _compose-ps

help: ## show targets
	@echo "AIlourOS commands (VENV=$(VENV)):"
	@grep -E '^[a-zA-Z0-9_.-]+:.*?##' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?##"}; {printf "  %-20s %s\n", $$1, $$2}'

venv: ## create $(VENV)
	python3 -m venv $(VENV)
	@echo "Next: make install"

_ensure-venv:
	@if [ ! -d "$(VENV)" ]; then python3 -m venv $(VENV); fi

_install-python:
	$(PIP) install -U pip
	$(PIP) install -r requirements.txt

install: _ensure-venv _install-python ## pip install -r requirements.txt

submodules: ## init/update required git submodules
	@if [ -f .gitmodules ]; then \
		echo "Ensuring git submodules are available..."; \
		git submodule update --init --recursive; \
	fi

lint: ## flake8 + import-linter + mypy
	$(PY) -m flake8 .
	$(LINT_IMPORTS)
	$(PY) -m mypy backend/ --ignore-missing-imports --no-error-summary 2>&1 | tail -5 || true
	$(PY) scripts/gen_env_docs.py || true

test: ## pytest
	$(PY) -m pytest --maxfail=1 --disable-warnings -q

test-security: ## security tests only
	$(PY) -m pytest tests/security/ --maxfail=1 --disable-warnings -q

ci: frontend-lint ## lint + tests (CI)
	$(PY) -m flake8 .
	$(LINT_IMPORTS)
	$(PY) -m pytest --maxfail=1 --disable-warnings -q

_frontend-ensure-deps: submodules
	@if [ ! -f "$(FRONTEND_NPM_STAMP)" ]; then $(MAKE) frontend-install; fi

run: start frontend-build ## local dev server (uvicorn + built UI, with workspace/command access enabled)
	@export SWARM_ALLOW_WORKSPACE_WRITE=1; \
	export SWARM_ALLOW_COMMAND_EXEC=1; \
	echo "Starting dev server with SWARM_ALLOW_WORKSPACE_WRITE=1 and SWARM_ALLOW_COMMAND_EXEC=1"; \
	$(UVICORN) backend.UI.REST.app:app --host 0.0.0.0 --port 8000

pipeline: ## one DAG pass
	$(PY) -m backend.App.orchestration.application.pipeline_graph

frontend-install: submodules ## install frontend dependencies
	@if [ -f "$(FRONTEND_LOCK)" ]; then \
		cd $(FRONTEND_DIR) && npm ci; \
	else \
		cd $(FRONTEND_DIR) && npm install; \
	fi

frontend-build: _frontend-ensure-deps ## Vue UI build
	cd $(FRONTEND_DIR) && npm run build

frontend-lint: _frontend-ensure-deps ## ESLint + TypeScript check + Prettier check
	cd $(FRONTEND_DIR) && npm run lint && npm run type-check && npm run format:check

e2e: _frontend-ensure-deps ## Playwright E2E (needs running server)
	cd $(FRONTEND_DIR) && npx playwright test

_compose-up:
	docker compose up -d --build

_compose-down:
	docker compose down

_compose-ps:
	@docker compose ps --format "$(COMPOSE_PS_FORMAT)"

# ---------------------------------------------------------------------------
# Docker Compose shortcuts
# ---------------------------------------------------------------------------

start: ## start the full docker-compose stack
	docker compose pull --ignore-buildable
	$(MAKE) _compose-up
	@echo ""
	@echo "Services started. UI: http://localhost:8000/ui"
	$(MAKE) _compose-ps

stop: ## stop the docker-compose stack
	$(MAKE) _compose-down

logs: ## follow logs
	docker compose logs -f --tail=100

ps: ## service status
	$(MAKE) _compose-ps

restart: ## restart all services
	docker compose restart
	$(MAKE) _compose-ps

# ---------------------------------------------------------------------------
# Quick setup
# ---------------------------------------------------------------------------

models: ## auto-detect hardware and pull best Ollama models
	@bash scripts/setup_models.sh

quickstart: submodules _ensure-venv frontend-install models ## first-run setup: submodules + deps + models + start
	@echo "=== AIlourOS Quickstart ==="
	$(PIP) install -U pip -q
	$(PIP) install -r requirements.txt -q
	@echo ""
	@echo "Setup complete. Starting services..."
	$(MAKE) _compose-up
	@echo ""
	@echo ">>> Open http://localhost:8000/ui"
