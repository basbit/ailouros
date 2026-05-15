SHELL := /bin/bash
.DEFAULT_GOAL := help

VENV               ?= .venv
FRONTEND_DIR       := frontend
FRONTEND_NPM_STAMP := $(FRONTEND_DIR)/node_modules/.package-lock.json
FRONTEND_NVMRC     := $(FRONTEND_DIR)/.nvmrc
NVM_DIR            ?= $(HOME)/.nvm
FRONTEND_NODE_ENV  := if [ -s "$(NVM_DIR)/nvm.sh" ] && [ -f "$(FRONTEND_NVMRC)" ]; then . "$(NVM_DIR)/nvm.sh"; nvm use --silent "$$(cat "$(FRONTEND_NVMRC)")" >/dev/null; fi;
PY                 := $(VENV)/bin/python
PIP                := $(VENV)/bin/pip
UVICORN            := $(VENV)/bin/uvicorn
LINT_IMPORTS       := $(VENV)/bin/lint-imports
export PYTHONPATH  := $(CURDIR)

.PHONY: help venv install lint gen-env-docs test test-security test-mutation ci \
	smoke smoke-fast install-embeddings pipeline \
	frontend-install frontend-build frontend-lint e2e \
	start stop restart logs ps models submodules run quickstart \
	_ensure-venv _frontend-ensure-deps _compose-up _compose-ps

help: ## show targets
	@grep -E '^[a-zA-Z0-9_.-]+:.*?##' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?##"}; {printf "  %-20s %s\n", $$1, $$2}'

venv: ## create $(VENV)
	python3 -m venv $(VENV)

_ensure-venv:
	@if [ ! -d "$(VENV)" ]; then python3 -m venv $(VENV); fi

install: _ensure-venv ## install Python dependencies
	$(PIP) install -U pip
	$(PIP) install -r requirements.txt

submodules: ## init/update git submodules
	@if [ -f .gitmodules ]; then git submodule update --init --recursive; fi

bump-frontend: ## fast-forward frontend submodule pointer to tip of origin/main
	@git submodule update --init --remote --recursive -- frontend
	@pinned=$$(git ls-files -s frontend | awk '{print $$2}'); \
	 tip=$$(cd frontend && git rev-parse HEAD); \
	 if [ "$$pinned" = "$$tip" ]; then \
	   echo "frontend: already at $$tip"; \
	 else \
	   git add frontend; \
	   echo "frontend: bumped $$pinned → $$tip (stage only; run 'git commit' to record)"; \
	 fi

lint: ## flake8 + import-linter + mypy + hardcoded-constants audit
	$(PY) -m flake8 .
	$(LINT_IMPORTS)
	@MYPY_COUNT=$$($(PY) -m mypy backend/ --ignore-missing-imports --explicit-package-bases --no-error-summary 2>&1 | grep -c "^backend/" || true); \
	MYPY_MAX=$${SWARM_MYPY_MAX:-20}; \
	if [ $$MYPY_COUNT -gt $$MYPY_MAX ]; then \
		$(PY) -m mypy backend/ --ignore-missing-imports --explicit-package-bases --no-error-summary 2>&1 | tail -20; \
		echo "mypy: $$MYPY_COUNT errors exceed threshold $$MYPY_MAX (override: SWARM_MYPY_MAX)"; \
		exit 1; \
	fi; \
	echo "mypy: $$MYPY_COUNT / $$MYPY_MAX"
	@AUDIT_MAX=$${SWARM_HARDCODE_MAX:-90}; \
	$(PY) scripts/audit/find_hardcoded_constants.py backend --max-findings $$AUDIT_MAX > /tmp/swarm_audit.log 2>&1; \
	AUDIT_EXIT=$$?; \
	AUDIT_COUNT=$$(grep -E "^hardcoded literal audit — [0-9]+ finding" /tmp/swarm_audit.log | sed -E 's/[^0-9]*([0-9]+).*/\1/' | head -n 1); \
	[ -z "$$AUDIT_COUNT" ] && AUDIT_COUNT=0; \
	if [ $$AUDIT_EXIT -ne 0 ]; then \
		tail -20 /tmp/swarm_audit.log; \
		echo "hardcode audit: $$AUDIT_COUNT findings exceed threshold $$AUDIT_MAX (override: SWARM_HARDCODE_MAX)"; \
		exit 1; \
	fi; \
	echo "hardcode audit: $$AUDIT_COUNT / $$AUDIT_MAX"

gen-env-docs: ## regenerate docs/AIlourOS.md env-var section
	$(PY) scripts/gen_env_docs.py

test: ## pytest (excludes smoke + playwright runtime tests)
	$(PY) -m pytest --maxfail=1 --disable-warnings -q \
		--ignore=tests/smoke \
		--ignore=tests/playwright

test-playwright: ## pytest playwright runtime tests (require Chromium installed)
	$(PY) -m pytest tests/playwright/ --maxfail=1 --disable-warnings -q


test-security: ## security tests only
	$(PY) -m pytest tests/security/ --maxfail=1 --disable-warnings -q

test-mutation: ## mutation testing (nightly; install requirements-mutation.txt first)
	@if ! $(PY) -c 'import mutmut' >/dev/null 2>&1; then \
		echo "test-mutation: mutmut not installed. Run: $(PIP) install -r requirements-mutation.txt"; \
		exit 1; \
	fi
	@if [ -z "$$SWARM_MUTATION_TARGETS" ]; then \
		echo "test-mutation: SWARM_MUTATION_TARGETS env var is required (comma-separated paths)"; \
		exit 1; \
	fi
	SWARM_MUTATION_TARGETS=$$SWARM_MUTATION_TARGETS \
		$(PY) -m mutmut run --paths-to-mutate $$SWARM_MUTATION_TARGETS \
		--tests-dir tests \
		--runner "python -m pytest -x --timeout=$${SWARM_MUTATION_PER_MUTANT_SEC:-5}"
	$(PY) -m mutmut results --json

smoke-e2e: ## golden-path e2e smoke (REST roundtrip)
	SWARM_SHARED_HISTORY_ENABLED=1 $(PY) scripts/e2e/smoke.py

ci: frontend-lint lint test smoke-e2e ## frontend-lint + lint + tests + e2e smoke

install-embeddings: _ensure-venv ## install sentence-transformers for smoke tests
	@$(PY) -c 'import sentence_transformers' >/dev/null 2>&1 || \
		$(PIP) install 'sentence-transformers>=2.7,<6'
	@$(PY) -c 'import pytest_timeout' >/dev/null 2>&1 || \
		$(PIP) install 'pytest-timeout>=2.3,<3'

smoke: install-embeddings ## smoke suite: pipeline (Ollama) + real-model retrieval
	SWARM_SMOKE=1 $(PY) -m pytest tests/smoke \
		-v --tb=short --timeout=1800 --timeout-method=thread -x

smoke-fast: install-embeddings ## smoke suite: real-model retrieval only (no Ollama)
	SWARM_SMOKE=1 $(PY) -m pytest tests/smoke \
		-v --tb=short --timeout=600 --timeout-method=thread

pipeline: ## one DAG pass
	$(PY) -m backend.App.orchestration.application.pipeline_graph

_frontend-ensure-deps: submodules
	@if [ ! -f "$(FRONTEND_NPM_STAMP)" ]; then $(MAKE) frontend-install; fi

frontend-install: submodules ## npm ci / npm install
	@if [ -f "$(FRONTEND_DIR)/package-lock.json" ]; then \
		$(FRONTEND_NODE_ENV) cd $(FRONTEND_DIR) && npm ci; \
	else \
		$(FRONTEND_NODE_ENV) cd $(FRONTEND_DIR) && npm install; \
	fi

frontend-build: _frontend-ensure-deps ## Vue UI build
	$(FRONTEND_NODE_ENV) cd $(FRONTEND_DIR) && npm run build

frontend-lint: _frontend-ensure-deps ## ESLint + TypeScript + Prettier check
	$(FRONTEND_NODE_ENV) cd $(FRONTEND_DIR) && npm run ci

e2e: _frontend-ensure-deps ## Playwright E2E (needs running server)
	$(FRONTEND_NODE_ENV) cd $(FRONTEND_DIR) && npm run e2e

_compose-up:
	docker compose up -d --build

_compose-ps:
	@docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"

start: ## start docker-compose stack
	docker compose pull --ignore-buildable
	$(MAKE) _compose-up
	$(MAKE) _compose-ps

stop: ## stop docker-compose stack
	docker compose down

restart: ## restart all services
	docker compose restart
	$(MAKE) _compose-ps

logs: ## follow logs
	docker compose logs -f --tail=100

ps: ## service status
	$(MAKE) _compose-ps

models: ## pull Ollama models (auto-detect hardware)
	@bash scripts/setup_models.sh

run: start frontend-build ## dev server: uvicorn + built UI
	SWARM_ALLOW_WORKSPACE_WRITE=1 SWARM_ALLOW_COMMAND_EXEC=1 \
		$(UVICORN) backend.App.shared.infrastructure.rest.app:app --host 0.0.0.0 --port 8000

quickstart: submodules _ensure-venv frontend-install models ## first-run setup
	$(PIP) install -U pip -q
	$(PIP) install -r requirements.txt -q
	$(MAKE) _compose-up
