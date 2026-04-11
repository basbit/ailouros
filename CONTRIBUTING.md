# Contributing to AIlourOS

Thanks for helping improve AIlourOS.

## Before you start

- Use Python 3.11+
- Use Node.js 18+
- Clone with submodules:

```bash
git clone --recurse-submodules https://github.com/basbit/ailouros.git
cd ailouros
```

If you already cloned the repo, initialize submodules with:

```bash
git submodule update --init --recursive
```

## Local setup

1. Create the Python environment:

```bash
make venv
make install
```

2. Install frontend dependencies:

```bash
make frontend-install
```

3. Create your local config:

```bash
cp .env.example .env
```

Keep `SWARM_ALLOW_COMMAND_EXEC` and `SWARM_ALLOW_WORKSPACE_WRITE` disabled unless you explicitly need them for a trusted local workflow.

## Development workflow

- Create a topic branch for each change
- Keep pull requests focused and reviewable
- Add or update tests for behavior changes
- Update docs when commands, configuration, or user-visible behavior changes

## Quality checks

Run these before opening a PR:

```bash
make ci
```

Useful targeted commands:

```bash
make test
make frontend-lint
make e2e
```

## Code style

- Python: follow `flake8`, import-linter, and existing typing patterns
- Frontend: follow ESLint, TypeScript, and Prettier checks from `frontend/package.json`
- Prefer small, composable changes over broad refactors unless the PR is explicitly scoped for that

## Pull requests

- Explain the problem, not just the code change
- Include validation steps
- Link related issues when applicable
- Call out breaking changes, migrations, or security implications clearly

## Reporting bugs

Use the GitHub issue templates and include:

- what you expected
- what happened instead
- reproducible steps
- environment details
- logs or screenshots when helpful
