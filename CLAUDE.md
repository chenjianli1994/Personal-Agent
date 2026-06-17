# CLAUDE.md

This file gives Claude Code and similar coding agents repository-specific guidance for `PersonalAgent`.

## Project

PersonalAgent is a self-contained, single-user local assistant.

- Backend: `personal_agent/`, Python + FastAPI.
- Frontend: `frontend/`, React + Vite.
- Storage: local SQLite, usually `.personal_agent/agent.db`.
- Primary language for user-facing text: Chinese.
- Tests run offline by default through the fake LLM provider.

Read these first in a new session:

1. `PROJECT_HANDOFF.md`
2. `AGENTS.md`
3. `README.md`
4. `personal_agent/app.py`
5. `personal_agent/runtime.py`
6. `personal_agent/routes.py`
7. `personal_agent/core/database.py`

## Commands

Backend setup and tests:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -e .[dev]
.\.venv\Scripts\python.exe -m pytest -q
```

Run backend with the local fake provider:

```powershell
.\.venv\Scripts\python.exe -m personal_agent serve --workspace . --db .personal_agent\agent.db --port 7870 --fake-llm
```

Frontend:

```powershell
cd frontend
npm install
npm run dev
npm run typecheck
```

Current verification result:

- `pytest -q`: `65 passed`
- `npm run typecheck`: passes

## Architecture

Main request flow:

```text
cli.py -> app.py -> bootstrap.py -> routes.py -> runtime.py -> feature modules -> personal_agent/core/**
```

Important modules:

- `bootstrap.py`: loads local env, initializes DB, creates the single local project, seeds default document skills.
- `runtime.py`: chat-turn orchestrator.
- `intent_router.py`: routes user intent.
- `policy_guard.py`: prevents unsupported side effects.
- `artifact_generation.py` / `artifact_drafts.py` / `artifact_quality.py`: draft generation, revisions, and quality checks.
- `knowledge_learning.py` / `learning_reflector.py` / `skill_reflector.py`: governed learning and skill update candidates.
- `core/llm_gateway.py`: all model calls go through `PersonalLLMGateway`.
- `core/fake_llm_provider.py`: deterministic local fixture for tests and offline smoke runs.
- `core/database.py`: single DB schema and compatibility migrations.
- `core/codebase/`: local code indexing, retrieval, impact analysis, candidate patch planning, and controlled validation.

## Guardrails

- `personal_agent/content_guard.py` is the only source for retired terms and retired project input keys.
- Do not duplicate that word list in tests, docs, or business logic.
- Tests that need polluted legacy data should import `FORBIDDEN_PERSONAL_TERMS` or `RETIRED_PROJECT_INPUT_KEYS`.
- `tests/test_personal_forbidden_scan.py` must continue scanning `personal_agent/core/`.
- Keep `collaboration.py` present unless the task explicitly removes that capability.
- Keep answer paths side-effect-light: no file writes, patch application, or durable records unless the route and policy explicitly allow it.
- Personal drafts are candidate artifacts. Do not silently turn candidate drafts into release records.
- `PersonalLLMGateway` is a safe code identifier; the guard is intentionally written not to flag it.

## Files Not To Commit

Do not commit local runtime or dependency files:

- `.personal_agent/`
- `.env`
- `.venv/`
- `frontend/node_modules/`
- `frontend/dist/`
- `.pytest_cache/`

## Working Style

- Prefer existing patterns over new abstractions.
- Use `rg` for search.
- Use `apply_patch` for manual edits.
- Run `pytest -q` and `npm run typecheck` before claiming a cross-module change is complete.
- For code review requests, lead with findings and file references.
