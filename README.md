# Ginger Night Walker

A local-first, Dockerized Playwright agent runtime built around **least-privilege intelligence**: each agent receives only the context, tools, state, and permissions required for its current assignment.

## Architecture

- **Orchestrator** — owns the user objective and task lifecycle.
- **Context compiler** — creates capability-specific, minimized task packets.
- **Policy engine** — denies operations not explicitly granted by the task's agent manifest.
- **Agent contracts** — validate inputs and outputs at every handoff.
- **Browser runtime** — owns the Playwright browser and serializes browser mutations.
- **Evidence store** — stores snapshots/results by opaque IDs; agents receive references only when authorized.

Agents do not inherit conversations or other agents' histories. Page content is treated as untrusted data, never as policy or system instructions.

## Quick start (Docker)

Requirements: Docker Engine/Desktop with Compose v2. The Playwright package and browser image are pinned together at v1.64.1.

```bash
cp .env.example .env
docker compose up --build -d
```

API: http://localhost:8000  
Health: http://localhost:8000/health  
OpenAPI: http://localhost:8000/docs

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
playwright install chromium
uvicorn nightwalker.api:app --reload
```

## Security model

- Default deny: an absent permission is denied.
- Task-scoped manifests: permissions are granted for one assignment only.
- No conversation-history access in agent input schemas.
- No direct agent-to-agent calls.
- Browser mutations are serialized by the runtime.
- Navigation is constrained by URL policy; localhost/private-network access is configurable.
- No arbitrary JavaScript evaluation capability is exposed to agents.
- Credentials are not injected into page context or agent prompts.

The browser container is for local development and testing. Playwright's standard Docker image is not intended for visiting untrusted websites. This is an initial development baseline, not a hardened hostile-tenant browser service. Before exposing it to untrusted users, add per-task OS/container isolation, network egress controls, secret management, authenticated API access, quotas, and a stronger browser sandbox.

## Current milestone

Milestone 0: runnable local foundation, policy enforcement, deterministic browser extraction, health endpoint, and tests.
