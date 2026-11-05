# Ginger Night Walker

A local-first, Dockerized Playwright agent runtime built around **least-privilege intelligence**. Ginger OS hosts the orchestrator, context compiler, policy engine and browser runtime. Language-model inference is sent to a separately hosted **remote Ollama server**.

## Architecture

- **Orchestrator** — owns the user objective and task lifecycle.
- **Context compiler** — creates capability-specific, minimized task packets.
- **Policy engine** — denies operations not explicitly granted by the task's agent manifest.
- **Agent contracts** — validate inputs and outputs at every handoff.
- **Remote Ollama client** — sends bounded prompts to the configured remote Ollama endpoint.
- **Model registry** — selects server-configured models by capability; clients cannot choose arbitrary models.
- **Browser runtime** — owns the Playwright browser and serializes browser mutations.

Agents do not inherit conversations or other agents' histories. Page content is untrusted data, never policy or system instructions. The model can propose plans; it cannot directly execute browser actions or grant permissions.

## Package management with uv

**uv is the only package manager used for Python dependency installation.** Do not use pip.

Install uv using the official Astral installer or your OS package manager, then run:

```bash
uv lock
uv sync --group dev
```

Commit the generated `uv.lock` file so local development and Docker builds use the same resolved dependency versions.

Run checks and the API with uv:

```bash
uv run pytest
uv run ruff check .
uv run uvicorn nightwalker.api:app --reload
```

For local browser setup, use the Playwright browser image (recommended) or install the browser with:

```bash
uv run playwright install chromium
```

## Remote Ollama setup

Ollama runs on a separate server. **Do not add an Ollama container to this Compose project and do not install models on Ginger OS.**

1. Configure the remote Ollama host to accept requests from the Ginger OS network.
2. Put a reverse proxy or private network gateway in front of Ollama. Prefer HTTPS, authentication, and source-IP restrictions. Do not expose an unauthenticated Ollama port to the public internet.
3. Pull the chosen models on the remote server, for example:

   ```bash
   ollama pull qwen2.5:7b
   ```

4. On Ginger OS, copy the environment template and configure the endpoint and model:

   ```bash
   cp .env.example .env
   ```

   Set `OLLAMA_BASE_URL` to the reachable remote URL (including any reverse-proxy path prefix), and set `OLLAMA_API_KEY` if the gateway requires a bearer token. Keep the key only in the untracked `.env` file.

5. Start Ginger Night Walker:

   ```bash
   docker compose up --build -d
   docker compose ps
   curl http://localhost:8000/health
   curl http://localhost:8000/health/ollama
   ```

The Docker build copies the pinned uv executable from the Astral uv image and runs `uv sync`; it does not invoke pip. The Docker build includes `uv.lock` automatically when it exists. Until the first lockfile is committed, a fresh checkout can still build by resolving from `pyproject.toml`; commit `uv.lock` after generating it with `uv lock` to make builds reproducible.

The Ollama client uses the Ollama `/api/chat` endpoint with a JSON Schema response format. The model must be available on the remote host before use.

### Model routing

Set a default model and optionally override each capability:

- `OLLAMA_MODEL_DEFAULT`
- `OLLAMA_MODEL_NAVIGATE`
- `OLLAMA_MODEL_INTERACT`
- `OLLAMA_MODEL_EXTRACT`
- `OLLAMA_MODEL_RECOVER`

If a capability-specific variable is blank, the default model is used. Model names are configuration on the Ginger server, not supplied by the task caller.

## Agent inference API

Submit a task to `POST /tasks/agent`:

```json
{
  "task_id": "demo-extract-001",
  "capability": "extract",
  "objective": "Extract the product names from the supplied page evidence",
  "input_data": {
    "dom_snapshot": "<main><h1>Products</h1></main>",
    "url": "https://example.com"
  },
  "requirements": {
    "fields": ["name"],
    "output_schema": "A JSON object containing a products array"
  }
}
```

The current inference endpoint returns a validated **proposal** with status `planned` or `blocked`. It does not execute browser actions. The browser extraction endpoint remains a separate deterministic operation. The task contract deliberately has no caller-controlled permissions field; a trusted permission broker must issue grants before any future action executor is enabled.

## Security model and limitations

- Default deny: an absent permission is denied by the policy engine.
- Context is allowlisted per capability; no conversation history is passed to agents.
- No direct agent-to-agent calls.
- Model selection and credentials are server-side configuration.
- The model receives no Ollama API key.
- Model-generated evidence references are rejected.
- Model output must match the Pydantic task result schema and the submitted task identity.
- Ollama inference has a configurable timeout and output-token limit.

The current `/tasks/agent` endpoint is a development integration, **not a public multi-user API**. Keep the service bound to localhost or a trusted private network until authentication, per-caller authorization, quotas, and audit logging are implemented. A remote Ollama URL is trusted operator configuration; use a controlled HTTPS endpoint, not a user-provided URL.

The browser container is for local development and testing. Playwright's standard Docker image is not intended for visiting untrusted websites. This is an initial development baseline, not a hardened hostile-tenant browser service. Before exposing it to untrusted users, add per-task OS/container isolation, network egress controls, secret management, authenticated API access, quotas, and a stronger browser sandbox.
