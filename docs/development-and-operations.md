# Development and operations

## 1. Runtime topology

- Ginger OS / Docker host runs FastAPI, Playwright Chromium and the React UI.
- Remote Ollama serves model inference; no Ollama container is part of this Compose stack.
- Browser execution defaults to local Playwright. Set `BROWSER_BACKEND=steel` to use the self-hosted Steel service in Compose. `STEEL_API_KEY` is only needed if the configured Steel endpoint requires authentication.
- The default Compose binding is `127.0.0.1:8000`.
- Task records and resumable checkpoints are persisted as atomic JSON snapshots under `EVIDENCE_DIR/tasks` (Docker default: `/app/evidence/tasks`). Evidence JSON, append-only scratchpad notes and per-task Markdown goal checklists share the same persistent volume. The checklist is regenerated from the evidence-gated goal ledger and supplied to research planning, goal verification and final synthesis.

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

Task records, scratchpad notes, generated Markdown checklists, evidence and round checkpoints persist on the Compose named volume `nightwalker_evidence`. On startup, queued/running tasks are restored and resumed from their last checkpoint. Keep the volume when recreating containers; `docker compose down -v` deletes it.

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
| `OLLAMA_BASE_URL` | Default remote Ollama API base URL | Operator-controlled trusted endpoint; fallback for role-specific URLs |
| `OLLAMA_BASE_URL_DEFAULT` | Default-role Ollama endpoint override | Optional; falls back to `OLLAMA_BASE_URL` |
| `OLLAMA_BASE_URL_DECIDER` | Decider/research-planner endpoint | Optional; falls back to `OLLAMA_BASE_URL` |
| `OLLAMA_BASE_URL_GOAL_STATE` | Goal criteria/progress endpoint | Optional; falls back to `OLLAMA_BASE_URL` |
| `OLLAMA_BASE_URL_SYNTHESIS` | Research synthesis endpoint | Optional; falls back to `OLLAMA_BASE_URL` |
| `OLLAMA_BASE_URL_NAVIGATE`, `_INTERACT`, `_EXTRACT`, `_RECOVER` | Capability-specific inference endpoints | Optional; each falls back to `OLLAMA_BASE_URL` |
| `OLLAMA_API_KEY` | Optional bearer credential | Secret; never commit |
| `OLLAMA_KEEP_ALIVE` | Ollama model residency after each request (default `0`) | `0` unloads after each call to reduce peak RAM; increase only if the host can keep both models loaded |
| `OLLAMA_MODEL_DEFAULT` | Main model for planning, navigation, interaction and synthesis (default `qwen3.5:9b`) | Must exist on remote Ollama |
| `OLLAMA_MODEL_LIGHT` | Smaller model for extraction, recovery and goal-progress proposals (default `qwen3.5:4b`) | Must exist on remote Ollama; Python validates evidence and outputs |
| `NAVIGATION_TIMEOUT_MS` | Browser navigation timeout | Keep bounded |
| `BROWSER_BACKEND` | Browser execution provider (`local` or `steel`) | Compose defaults to self-hosted Steel; `local` uses Playwright Chromium |
| `STEEL_API_KEY` | Optional Steel API credential | Leave blank for an unauthenticated self-hosted instance; never commit secrets |
| `STEEL_API_BASE_URL` | Steel Sessions REST API base | Self-hosted Compose default: `http://steel-browser:3000/v1` |
| `STEEL_SESSION_TIMEOUT_MS` | Steel session lifetime in milliseconds | Defaults to 300000 (5 minutes); keep bounded |
| `EVIDENCE_DIR` | Evidence, scratchpad and default task-state root | Docker default `/app/evidence`; must be on persistent storage |
| `TASK_STATE_DIR` | Optional override for atomic task JSON snapshots | Defaults to `EVIDENCE_DIR/tasks` |

Night Walker intentionally routes across two model names only: `OLLAMA_MODEL_DEFAULT` (main) and `OLLAMA_MODEL_LIGHT` (small). Planning and synthesis use the main model; extraction, recovery and goal-progress proposals use the light model. The two tiers may share one Ollama endpoint. Do not set role-specific model overrides. Role-specific endpoint URLs remain optional for deployments that serve the same two models from different trusted endpoints. The experimental standalone integration has its own settings and is not part of this two-model runtime.

## 6. Agent identity and request tracing

Every model-service request carries explicit `X-Ginger-Agent`, `X-Ginger-Capability`, `X-Ginger-Operation` and `X-Ginger-Integration` headers. When known, requests also carry `X-Ginger-Model`, `X-Ginger-Task-Id` and `X-Ginger-Research-Round`. The standalone Goal-Aware Strands integration uses the same header convention, adds `X-Ginger-Step` for its per-run step counter, and identifies its integration separately. These are observability/routing metadata only: headers are not authentication, authorization, or permission grants. The orchestrator and PolicyEngine remain authoritative for access and browser actions.

## 7. API surface

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Service liveness |
| GET | `/health/ollama` | Remote inference connectivity |
| POST | `/tasks` | Submit natural-language objective (202) |
| GET | `/tasks` | List restored and in-memory tasks |
| GET | `/tasks/{task_id}` | Fetch task state, execution blueprint, assignments, progress and the current `checklist_markdown` |\n| GET | `/tasks/{task_id}/a2a` | Read task-scoped A2A messages |\n| POST | `/tasks/{task_id}/a2a` | Send a typed A2A message between agents allocated to this task |
| POST | `/tasks/{task_id}/clarify` | Supply clarification |
| POST | `/tasks/{task_id}/follow-up` | Add an instruction to an active research task for subsequent rounds |
| DELETE | `/tasks/{task_id}` | Cancel active, queued or clarification-waiting task; preserve partial report |
| POST | `/tasks/agent` | Legacy capability-specific proposal endpoint |
| POST | `/tasks/browser-extract` | Compatibility browser extraction endpoint |

FastAPI's OpenAPI schema is available at `/openapi.json`; Swagger UI is available at `/docs` unless disabled by deployment configuration.

## 8. Quality gates and coding standards

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

## 9. Test strategy

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

## 10. Operational checks

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
- Task not resuming: inspect `/app/evidence/tasks`, confirm the named volume is mounted, and check startup logs for task restore errors.
- Evidence missing: check `EVIDENCE_DIR`, mount and container write permissions.
- Browser blocked: inspect task limitations and policy logs; do not weaken URL policy to make a target load.

## 11. Known limitations

- Task snapshots and research checkpoints are durable and restart-resumable for the single-process service. This is atomic JSON storage, not a transactional multi-worker queue; do not run multiple Night Walker workers against the same task directory. A crash during a page inspection can retry that one source, bounded by the loop guard. Terminal and clarification-waiting tasks are restored but not auto-resumed.
- Research runs up to 30 bounded page-inspection rounds, starting from Bing search results or a user-supplied URL; discovered links are the candidate source pool. The Research Loop Guard canonicalises URL comparisons, deduplicates candidates, permits at most two attempts per URL, records bounded recent outcomes in task state/scratchpad, skips repeated or already visited sources, and falls back to another discovered candidate when a selected source is exhausted.
- Source selection is model-assisted but constrained to URLs actually observed by the browser. Discovery quality depends on search results and page links. Repeat detection is deterministic and task-local; it is not a general-purpose model-action verifier.
- The Goal State Manager tracks an immutable objective and up to twelve success criteria. The small model can propose completion only with an exact quote and evidence ID; Python verifies both against the current captured evidence. This is evidence linkage, not proof that the source itself is truthful.
- An optional Strands Decider can select only among browser-discovered source URLs. If unavailable or misconfigured, the Ollama planner remains the fallback. The integrated Strands adapter has no browser tools.
- Findings carry source/evidence IDs and visible-text excerpts; synthesis is bounded to those observations and is not yet a full fact-checking or source-authority engine.
- Active tasks accept follow-up instructions; follow-ups after completion require a new task.
- Cancellation preserves completed-round partial reports. Operator-stopped tasks remain stopped across restart; only tasks saved as queued/running are auto-resumed.
- Product price extraction is a narrow structured-data adapter.
- Browser interactions are read-only and do not support arbitrary click/type/form workflows.
- The application is not authenticated or hardened for multi-user hostile workloads.


## 7. Steel cloud browser backend

Night Walker can connect Playwright to a Steel-managed browser without changing its research planner, evidence schema, or task API. Set these values in the untracked `.env` file:

```dotenv
BROWSER_BACKEND=steel
STEEL_API_KEY=your-steel-api-key
STEEL_SESSION_TIMEOUT_MS=300000
```

Rebuild and restart the service after changing backend configuration. Steel sessions are created per browser workflow, connected using the session's returned WebSocket URL, and released after the workflow. If release fails, Steel's session timeout is the fallback cleanup mechanism. The API key is sent only to Steel and is not included in evidence records.

The existing Night Walker URL policy and request guard remain in place, but Steel is a remote browser provider—not a replacement for outbound network egress controls. Keep the container/network firewall or trusted egress proxy as the final control against SSRF and internal-network access. Validate the Steel backend with a real key in a non-production environment before switching a live deployment.
