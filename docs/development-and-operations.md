# Development and operations

## 1. Runtime topology

- Ginger OS / Docker host runs FastAPI, Playwright Chromium and the React UI.
- Remote Ollama serves model inference; no Ollama container is part of this Compose stack.
- The default Compose binding is `127.0.0.1:8000`.
- Task state is in process memory; evidence JSON and append-only scratchpad notes are persisted under `EVIDENCE_DIR` (Docker default: `/app/evidence`).

## 2. Prerequisites

- Docker Engine and Docker Compose plugin
- Git
- Node.js 22+ and npm for standalone frontend development
- uv 0.10.0 and Python 3.11 for local backend development
- Remote Ollama endpoint reachable from the container

## 3. Start with Docker

```bash
cp .env.example .env
# Edit .env; never commit secrets.
docker compose up --build -d
docker compose ps
curl -fsS http://localhost:8000/health
curl -fsS http://localhost:8000/health/ollama
```

To update an existing local checkout:

```bash
git pull --ff-only origin main
docker compose down
docker compose up --build -d
docker compose logs --tail=100 night-walker
```

The task list is in memory and will be cleared by a container restart. Evidence files persist only if the configured evidence directory is backed by a persistent volume.

## 4. Local development

Backend:

```bash
uv sync --group dev
uv run ruff check .
uv run pytest
uv run uvicorn nightwalker.api:app --reload
```

Frontend (separate terminal):

```bash
cd frontend
npm install
npm run dev
```

Vite proxies API requests to the local FastAPI service on port 8000.

## 5. Configuration

| Variable | Purpose | Guidance |
|---|---|---|
| `OLLAMA_BASE_URL` | Remote Ollama API base URL | Operator-controlled trusted endpoint |
| `OLLAMA_API_KEY` | Optional bearer credential | Secret; never commit |
| `OLLAMA_MODEL_DEFAULT` | Default inference model | Must exist on remote Ollama |
| `OLLAMA_MODEL_DECIDER` | Research planner override | Optional |\n| `OLLAMA_MODEL_GOAL_STATE` | Small model for success criteria and evidence progress (default `qwen3:0.6b`) | Must exist on remote Ollama |\n| `STRANDS_DECIDER_URL` | Optional Strands Decider base URL | Leave empty to use the Ollama research planner |\n| `STRANDS_DECIDER_PATH` | Strands endpoint path (default `/v1/systemone`) | Match the deployed Strands API |\n| `STRANDS_DECIDER_API_KEY` | Optional Strands bearer token | Separate from Ollama credentials |
| `OLLAMA_MODEL_NAVIGATE` | Navigation capability model | Falls back to default |
| `OLLAMA_MODEL_INTERACT` | Interaction capability model | Falls back to default |
| `OLLAMA_MODEL_EXTRACT` | Extraction capability model | Falls back to default |
| `OLLAMA_MODEL_RECOVER` | Recovery capability model | Falls back to default |
| `NAVIGATION_TIMEOUT_MS` | Browser navigation timeout | Keep bounded |
| `EVIDENCE_DIR` | Evidence output directory | Mount persistent storage if retention is required |

Check `.env.example` for the current supported values and defaults.

## 6. API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Service liveness |
| GET | `/health/ollama` | Remote inference connectivity |
| POST | `/tasks` | Submit natural-language objective (202) |
| GET | `/tasks` | List in-memory tasks |
| GET | `/tasks/{task_id}` | Fetch task state |
| POST | `/tasks/{task_id}/clarify` | Supply clarification |
| POST | `/tasks/{task_id}/follow-up` | Add an instruction to an active research task for subsequent rounds |
| DELETE | `/tasks/{task_id}` | Cancel active, queued or clarification-waiting task; preserve partial report |
| POST | `/tasks/agent` | Legacy capability-specific proposal endpoint |
| POST | `/tasks/browser-extract` | Compatibility browser extraction endpoint |

FastAPI's OpenAPI schema is available at `/openapi.json`; Swagger UI is available at `/docs` unless disabled by deployment configuration.

## 7. Quality gates and coding standards

CI currently runs:
- Python 3.11 dependency resolution with uv;
- Ruff lint;
- pytest;
- Vite production build.

Engineering rules:
- Python: type hints, Pydantic at trust boundaries, async I/O for network/browser operations, bounded inputs and outputs.
- TypeScript: strict compiler settings, explicit API types, controlled React state and accessible labels.
- Dependencies: update lockfile deliberately; avoid pip; pin browser version to the Playwright image.
- API: preserve backward compatibility where practical; version breaking report contract changes.
- Security: no model-controlled permissions; no arbitrary browser calls from model output.
- Tests: test policy denials, failure paths, task cancellation, evidence persistence, schema validation and frontend build.
- Documentation: update diagrams, API tables, standards and status matrices whenever boundaries or behavior change.

## 8. Test strategy

| Layer | Required coverage |
|---|---|
| Unit | URL policy, decision validation, report contracts, parsers, evidence IDs |
| Component | BrowserRuntime with fake pages/contexts, task manager lifecycle |
| Integration | FastAPI endpoints, Ollama envelope handling, evidence persistence |
| Frontend | report rendering for generic findings, arbitrary structured data, empty/partial reports |
| Security | private IPs, redirects, DNS edge cases, prompt injection, oversized content |
| End-to-end | objective → plan → browser → evidence → report |
| Regression | previously fixed clarification focus, empty model response, missing target URL |

A passing build is not evidence that a browser security boundary is complete. Keep security tests separate and explicit.

## 9. Operational checks

```bash
docker compose ps
docker compose logs --tail=100 night-walker
curl -i http://localhost:8000/
curl -fsS http://localhost:8000/health
curl -i http://localhost:8000/health/ollama
```

Common diagnoses:
- UI assets missing: rebuild the Docker image; confirm `nightwalker/static/ui` contains Vite output.
- Ollama unavailable: check endpoint routing, proxy authentication and model availability from inside the container.
- Task disappeared: expected after restart with current in-memory TaskManager.
- Evidence missing: check `EVIDENCE_DIR`, mount and container write permissions.
- Browser blocked: inspect task limitations and policy logs; do not weaken URL policy to make a target load.

## 10. Known limitations

- Task state is not durable; scratchpad and evidence files persist but are not yet sufficient to resume a task after restart.
- Research runs up to 30 bounded page-inspection rounds, starting from Bing search results or a user-supplied URL; discovered links are the candidate source pool.
- Source selection is model-assisted but constrained to URLs actually observed by the browser. Discovery quality depends on search results and page links.
- The Goal State Manager tracks an immutable objective and up to six success criteria. The small model can propose completion only with an exact quote and evidence ID; Python verifies both against the current captured evidence. This is evidence linkage, not proof that the source itself is truthful.\n- An optional Strands Decider can select only among browser-discovered source URLs. If unavailable or misconfigured, the Ollama planner remains the fallback. The integrated Strands adapter has no browser tools.\n- Findings carry source/evidence IDs and visible-text excerpts; synthesis is bounded to those observations and is not yet a full fact-checking or source-authority engine.
- Active tasks accept follow-up instructions; follow-ups after completion require a new task.
- Cancellation preserves completed-round partial reports. Full task state remains in memory, so a service restart does not automatically resume the research loop.
- Product price extraction is a narrow structured-data adapter.
- Browser interactions are read-only and do not support arbitrary click/type/form workflows.
- The application is not authenticated or hardened for multi-user hostile workloads.
