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
| `OLLAMA_MODEL_DEFAULT` | Main model for planning, navigation planning and synthesis (default `qwen3.5:9b`) | Must exist on remote Ollama |
| `OLLAMA_MODEL_LIGHT` | Smaller model for site interactions, extraction, recovery and goal-progress (default `qwen3.5:4b`) | Must exist on remote Ollama; keep this tiny for HTML/ref click-type proposals |
| `NAVIGATION_TIMEOUT_MS` | Browser navigation timeout | Keep bounded |
| `BROWSER_BACKEND` | Browser execution provider (`local` or `steel`) | Compose defaults to self-hosted Steel; `local` uses Playwright Chromium |
| `STEEL_API_KEY` | Optional Steel API credential | Leave blank for an unauthenticated self-hosted instance; never commit secrets |
| `STEEL_API_BASE_URL` | Steel Sessions REST API base | Self-hosted Compose default: `http://steel-browser:3000/v1` |
| `STEEL_SESSION_TIMEOUT_MS` | Steel session lifetime in milliseconds | Defaults to 300000 (5 minutes); keep bounded |
| `EVIDENCE_DIR` | Evidence, scratchpad and default task-state root | Docker default `/app/evidence`; must be on persistent storage |
| `TASK_STATE_DIR` | Optional override for atomic task JSON snapshots | Defaults to `EVIDENCE_DIR/tasks` |
| `AUDIT_MAX_PAGES` | Maximum page inspections for SEO/QA/combined audits | Defaults to 200; accepted range 1-1,000 |

Night Walker intentionally routes across two model names only: `OLLAMA_MODEL_DEFAULT` (main) and `OLLAMA_MODEL_LIGHT` (small). Planning and synthesis use the main model; Steel site interactions (two-stage tool select + args), extraction, recovery and goal-progress use the light model. Point `OLLAMA_MODEL_LIGHT` at a smaller HTML-logic model if wanted — Steel proposals use a capped context (`OLLAMA_STEEL_NUM_CTX`, default 4096) so they stay cheap. The two tiers may share one Ollama endpoint. Do not set role-specific model overrides. Role-specific endpoint URLs remain optional for deployments that serve the same two models from different trusted endpoints. The experimental standalone integration has its own settings and is not part of this two-model runtime.

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

The GitHub Actions CI workflow is intentionally disabled during beta. Before deployment, run locally:
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

For a live audit through the running local service, first rebuild/restart Night Walker, then run:

```bash
uv run python scripts/live_audit_smoke.py --url https://weylandts.co.za --workflow seo --output /tmp/nightwalker-seo-report.json
uv run python scripts/live_audit_smoke.py --url https://weylandts.co.za --workflow qa --output /tmp/nightwalker-qa-report.json
```

The script submits through `POST /tasks`, polls `GET /tasks/{task_id}`, and prints the final status, finding count and deterministic audit summary. A `blocked` result is a completed execution with unmet verification criteria, not a successful audit. Preserve the JSON report and execution logs when diagnosing a live run.

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
- SEO/QA/combined tasks use deterministic same-origin crawling with a default 200-page budget (`AUDIT_MAX_PAGES`, maximum 1,000), robots.txt enforcement, bounded sitemap-index discovery, duplicate-page analysis and evidence-backed internal-link checks. Coverage is reported against discovered and sampled sitemap URLs; it is not exhaustive site coverage. General research remains deferred in the initial product phase, although legacy research code remains in the repository for compatibility.
- Source selection is model-assisted but constrained to URLs actually observed by the browser. Discovery quality depends on search results and page links. Repeat detection is deterministic and task-local; it is not a general-purpose model-action verifier.
- The Goal State Manager tracks an immutable objective and up to twelve success criteria. The small model can propose completion only with an exact quote and evidence ID; Python verifies both against the current captured evidence. This is evidence linkage, not proof that the source itself is truthful.
- An optional Strands Decider can select only among browser-discovered source URLs. If unavailable or misconfigured, the Ollama planner remains the fallback. The integrated Strands adapter has no browser tools.
- Findings carry source/evidence IDs and visible-text excerpts; synthesis is bounded to those observations and is not yet a full fact-checking or source-authority engine.
- Active tasks accept follow-up instructions; follow-ups after completion require a new task.
- Cancellation preserves completed-round partial reports. Operator-stopped tasks remain stopped across restart; only tasks saved as queued/running are auto-resumed.
- Product price extraction is a narrow structured-data adapter.
- Browser interactions are read-only and do not support arbitrary click/type/form workflows.
- The application is not authenticated or hardened for multi-user hostile workloads.


## 7. Steel MCP experiment

Night Walker can start the official Steel MCP server as a child process while retaining the existing Playwright/CDP runtime. This is deliberately opt-in so the current browser path remains available for comparison.

Set:

```dotenv
STEEL_MCP_ENABLED=true
STEEL_MCP_PROFILE=browse
STEEL_MCP_BASE_URL=http://steel-browser:3000/v1
```

The image builds the official `steel-dev/steel-mcp-server` at a pinned source revision and runs its stdio transport with `STEEL_LOCAL=true`. No Steel Cloud account or API key is required for the self-hosted Steel service. The MCP adapter discovers the live tool catalog at startup. Check `GET /health/steel-mcp` for connection status and discovered tool names.

A bounded connectivity/browser smoke test is available at `POST /tools/steel-mcp/smoke-test` with JSON `{"url":"https://example.com"}`. It creates a session, navigates, captures a snapshot, and releases the session. URL policy is applied before navigation. This endpoint is for validating MCP transport and Steel browser behaviour; the normal mission-plan executor continues to use the existing BrowserRuntime until the MCP execution adapter is separately enabled and compared.

Keep `STEEL_MCP_ENABLED=false` to use only the existing Playwright/CDP path. Both implementations are retained during the experiment.

## 7. Self-hosted Steel browser backend

The Compose stack runs the official single-image Steel Browser service. Night Walker creates a session through the self-hosted Sessions API at `http://steel-browser:3000/v1`, connects to the returned CDP WebSocket URL, and releases the session after the workflow. A Steel API key is not required for the default local service; set `STEEL_API_KEY` only if you deliberately add authentication or point at a protected endpoint. The API and CDP ports are bound to loopback on the host; do not expose CDP port 9223 publicly.

```dotenv
BROWSER_BACKEND=steel
STEEL_API_BASE_URL=http://steel-browser:3000/v1
STEEL_API_KEY=
STEEL_SESSION_TIMEOUT_MS=300000
```

Rebuild and restart after changing backend configuration. If session release fails, Steel's session timeout is the fallback cleanup mechanism. Steel's self-hosting guide recommends at least 4 GB RAM and 10 GB free disk; the browser container is separate from Night Walker's 2 GB container limit. If the host cannot sustain that, use `BROWSER_BACKEND=local` to run Playwright in Night Walker instead, accepting the local-browser trade-offs.

The existing Night Walker URL policy and request guard remain in place, but they are not a complete SSRF boundary: browser redirect handling can bypass per-request route checks. Steel is not a replacement for outbound network egress controls. Keep a host/container firewall or trusted egress proxy as the final control against internal-network access. Do not treat this deployment as hardened against hostile websites until egress enforcement is implemented and tested.


## Beta CI pause

GitHub Actions CI is intentionally paused during the beta iteration period. The workflow source is preserved at `.github/workflows/ci.yml.disabled`, which GitHub does not recognise as an active workflow. To re-enable CI, restore the filename to `.github/workflows/ci.yml`. Until then, run lint, backend tests and the frontend production build locally before deploying.

## Mission plan execution ledger

Each natural-language mission can own an execution plan. The plan is persisted inside the task snapshot and rendered into the task-scoped Markdown checklist under `EVIDENCE_DIR/checklists`. Step transitions and their history are written through the Task Manager; the Markdown file is a generated operator-readable ledger, not an independently editable source of truth.

A step is only ticked after the trusted runtime records evidence references and verifies the applicable output. Navigation can be verified by a captured page; structured extraction requires structured records; numeric comparison is performed in Python over extracted price values. The UI exposes the plan and step counts in the selected mission, and compact plan progress on mission cards.

Current beta scope is one plan per mission and sequential dependency scheduling. Independent worker execution, parallel step scheduling, arbitrary multi-plan revisions and a durable external A2A broker remain future work.
