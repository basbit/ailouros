# AIlourOS

![CI](https://github.com/basbit/ailouros/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/python-3.11+-blue.svg)

**[ailouros.io](https://ailouros.io)**

> **Status: active development** — Core pipeline is functional and used in daily development. APIs may change between releases. Feedback and contributions welcome.

Multi-agent pipeline (Python · FastAPI · LangGraph) — runs fully locally with Ollama or LM Studio, or routes to Anthropic Claude for cloud steps. Each pipeline step is followed by a **Reviewer** (LLM) and an optional **Human** approval gate.

## Who this is for

- Developers who want a local-first multi-agent coding pipeline
- Teams exploring structured AI workflows with explicit review and approval gates
- Builders who need an OpenAI-compatible API plus a browser UI for orchestration

## Repository layout

- `backend/` — FastAPI app, orchestration engine, domain logic, integrations
- `frontend/` — Vue 3 UI shipped as a git submodule
- `tests/` — backend unit and security-oriented tests

---

## How it works

A single task flows through a structured DAG:

```
User input
  → Clarify → PM → [Reviewer → Human gate]
              → BA ║ Architect → [Reviewer → Human gate]
              → Dev → [Reviewer → Human gate]
              → QA  → [Reviewer → Human gate]
              → Artifacts
```

Between steps the system passes **validated JSON artifacts** (not raw text), so each agent receives structured, verifiable input. Any failure is surfaced explicitly — there are no silent fallbacks (INV-1).

---

## Key features

- **Fully local** — Ollama or LM Studio; no data leaves your machine by default
- **Optional cloud routing** — send planning, build, or all steps to Anthropic Claude via `SWARM_ROUTE_*`
- **OpenAI-compatible API** — drop-in for Cursor, VS Code Continue, or any `/v1/chat/completions` client
- **MCP integration** — agents read/write the workspace via Model Context Protocol servers
- **Workspace context modes** — `retrieve` (MCP), `full`, `priority_paths`, `index_only`, and more
- **Autonomous features** — deep planning, self-verify, auto-retry, auto-approve, dream/consolidation
- **Human gates** — pause and approve/reject after any step; configurable per-role
- **Pattern memory** — cross-task learning stored across runs
- **Real-time streaming** — SSE + WebSocket events feed in the UI
- **Structured artifacts** — every pipeline run writes `pipeline.json`, per-agent outputs, and logs to `var/artifacts/<task_id>/`

---

## Pipeline topologies

| Topology | Behaviour |
|----------|-----------|
| `default` | Full DAG from `build_graph()` |
| `mesh` | Parallel Dev+QA subtasks (`SWARM_MAX_PARALLEL_TASKS`) |
| `ring` | Review → Human cycles built into each step |

Set via `agent_config.swarm.topology` in the API or in the UI.

---

## Clone (includes submodules)

```bash
git clone --recurse-submodules https://github.com/basbit/ailouros.git
cd ailouros
```

> If you already cloned without `--recurse-submodules`:
> ```bash
> git submodule update --init --recursive
> ```
>
> `make quickstart` also initializes required submodules automatically before installing dependencies.

---

## Security notice

> **Warning**
> Two environment variables enable powerful agent capabilities. Keep them disabled unless you understand the implications:
>
> | Variable | Default | Effect |
> |----------|---------|--------|
> | `SWARM_ALLOW_COMMAND_EXEC=1` | off | Agents can execute arbitrary shell commands |
> | `SWARM_ALLOW_WORKSPACE_WRITE=1` | off | Agents can read and write files on disk |
>
> **Never expose the API (`localhost:8000`) to an untrusted network while these are enabled.**

---

## ⚡ Quickstart (3 steps)

### Step 1 — Install a local LLM runtime

Pick one:

| Option | Install | Docs |
|--------|---------|------|
| **Ollama** (recommended, CLI) | `brew install ollama` · [ollama.com](https://ollama.com/download) | Auto CLI |
| **LM Studio** (GUI) | [lmstudio.ai](https://lmstudio.ai) | Enable *Local Server* in app |

> **Not sure which model fits your machine?** → [canirun.ai](https://www.canirun.ai/)

---

### Step 2 — Download the best model for your computer

```bash
make models
```

This script auto-detects your RAM / GPU and pulls the right model via Ollama:

| RAM | Recommended model |
|-----|------------------|
| 8 GB | `qwen2.5-coder:3b` |
| 16 GB | `qwen2.5-coder:7b` |
| 32 GB | `qwen2.5-coder:14b` |
| 64 GB+ | `qwen2.5-coder:32b` |

> Using **LM Studio**? Download the model manually in the app and enable *Local Server*. Then skip to Step 3.

---

### Step 3 — Start the project

```bash
make start
```

Open **http://localhost:8000/ui** — the UI is ready.

---

### First time? One command does it all

```bash
make quickstart
```

This runs: `git submodule update --init --recursive → venv → pip install → npm install → make models → docker compose up`.

---

## Requirements

- **Python** ≥ 3.11
- **Docker Desktop** (for Redis, Qdrant, Prometheus)
- **Node.js** ≥ 18 (for UI build)
- **Ollama** or **LM Studio** (local LLM backend)

---

## All Makefile targets

```bash
make help          # list all targets
make quickstart    # first-run: full setup + start
make models        # auto-detect hardware, pull Ollama models
make start         # docker compose build + up  →  http://localhost:8000/ui
make stop          # docker compose down
make logs          # follow logs
make ps            # service status
make restart       # restart all services
make run           # build UI + uvicorn :8000 (dev mode, workspace write/exec enabled)
make venv          # create .venv
make install       # pip install -r requirements.txt
make ci            # lint + pytest (CI)
make e2e           # Playwright E2E tests
```

---

## Services

| Service | URL |
|---------|-----|
| UI | http://localhost:8000/ui |
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |
| Redis | localhost:6379 |
| Qdrant | localhost:6333 |
| Prometheus | http://localhost:9090 |

---

## Configuration

```bash
cp .env.example .env   # already done by `make models` / `make quickstart`
```

Key settings in `.env`:

```env
# Model (auto-set by `make models`)
SWARM_MODEL=qwen2.5-coder:14b

# Provider routing (default: ollama)
SWARM_ROUTE_DEFAULT=ollama        # or: lmstudio, cloud

# Base URLs (defaults work out of the box)
OPENAI_BASE_URL=http://localhost:11434/v1    # Ollama
LMSTUDIO_BASE_URL=http://localhost:1234/v1   # LM Studio

# Context window (set to your model's n_ctx for best results)
SWARM_MODEL_CONTEXT_SIZE=0
```

### Cloud routing (Anthropic Claude)

Mix local and cloud models per pipeline phase:

```env
ANTHROPIC_API_KEY=sk-ant-...

# Route planning roles to Claude, keep build roles local
SWARM_ROUTE_PLANNING=cloud
SWARM_MODEL_CLOUD_PLANNING=claude-opus-4-5

SWARM_ROUTE_BUILD=ollama
SWARM_MODEL_BUILD=qwen2.5-coder:14b
```

Full environment variable reference: [`docs/AIlourOS.md § 11`](docs/AIlourOS.md).

---

## Use as API

AIlourOS exposes an OpenAI-compatible endpoint. Use it from any client:

**Cursor:**
```json
{
  "name": "ailouros-local",
  "baseUrl": "http://localhost:8000/v1",
  "apiKey": "local"
}
```

**curl:**
```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "swarm",
    "stream": true,
    "messages": [{"role": "user", "content": "Add pagination to the users list endpoint"}],
    "agent_config": {
      "workspace_root": "/path/to/your/project"
    }
  }'
```

Track progress: `GET /tasks/{task_id}` — returns status, step, and artifact paths.

---

## Autonomous features (K-phase)

Configure in the UI under **Autonomous Features** or via `agent_config.swarm.*`:

| Feature | API field | Description |
|---------|-----------|-------------|
| Deep planning | `deep_planning` | Extra planning pass before PM step |
| Self-verify | `self_verify` | Agent verifies its own output before proceeding |
| Auto-approve | `auto_approve` | Skip human gates (`always` / `timeout`) |
| Auto-retry | `auto_retry` | Retry failed steps up to `max_step_retries` |
| Dream/consolidation | `dream_enabled` | Background memory consolidation between runs |

---

## Architecture

**Stack:** Python 3.11+, FastAPI, LangGraph, Redis (or in-memory), Vue 3 + Vite

**Domain-Driven Design** — 5 bounded contexts:

| Context | Responsibility |
|---------|---------------|
| Orchestration | Pipeline, steps, retry, human gates, agents |
| Workspace | Context modes, file index, read/write/patches |
| Task Tracking | `task_id`, status, event history |
| Integrations | MCP, LLM routing, observability, memory |
| Scheduling | Timed pipeline execution |

Domain layer has **zero external dependencies** (no fastapi/redis/httpx/openai/anthropic in domain) — enforced by import-linter on every `make ci`.

---

## Development

```bash
make venv && make install    # Python env
make run                     # build UI + start server (hot reload)
make ci                      # lint + tests
make frontend-lint           # ESLint + TS check + Prettier
```

## Contributing

Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request. It covers local setup, submodules, code style, test commands, and the expected review flow.

Community expectations live in [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md). For security issues, please use [`SECURITY.md`](SECURITY.md) instead of filing a public issue.

---

## License

[MIT](LICENSE)
