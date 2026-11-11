FROM node:22-bookworm-slim AS steel-mcp-build
WORKDIR /app/steel-mcp-server
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git init && git remote add origin https://github.com/steel-dev/steel-mcp-server.git && git fetch --depth 1 origin f28a17571b10053e3df5e4bbbc4d3bc30f58ba39 && git checkout FETCH_HEAD
RUN node -e 'const fs=require("node:fs"); const p="src/core/steel/cdp.ts"; const s=fs.readFileSync(p,"utf8"); const a="const socket = new WebSocket(url, { maxPayload: 256 * 1024 * 1024 });"; const b="const socket = new WebSocket(url, { headers: { Host: process.env.STEEL_WS_HOST_HEADER || \"localhost\" }, maxPayload: 256 * 1024 * 1024 });"; if (!s.includes(a)) throw new Error("Pinned Steel CDP WebSocket constructor changed; review host-header patch"); fs.writeFileSync(p,s.replace(a,b));'
RUN npm ci --ignore-scripts && npm run build && npm prune --omit=dev

FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:0.10.0 AS uv

FROM mcr.microsoft.com/playwright/python:v1.64.1-noble
COPY --from=uv /uv /uvx /bin/
COPY --from=steel-mcp-build /usr/local/bin/node /usr/local/bin/node
COPY --from=steel-mcp-build /app/steel-mcp-server/dist /app/steel-mcp-server/dist
COPY --from=steel-mcp-build /app/steel-mcp-server/node_modules /app/steel-mcp-server/node_modules
COPY --from=steel-mcp-build /app/steel-mcp-server/package.json /app/steel-mcp-server/package.json
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --python "$(command -v python3)" --no-dev --no-editable --no-install-project
COPY . .
COPY --from=frontend-build /app/frontend/dist ./nightwalker/static/ui
RUN uv sync --python "$(command -v python3)" --no-dev --no-editable && \
    test -x /app/.venv/bin/python && \
    /app/.venv/bin/python --version && \
    /app/.venv/bin/python -c "import uvicorn, fastapi, playwright"
# Pin to UID/GID 1000 so the bind-mounted ./data/evidence volume (owned by the
# typical host developer account) stays writable. The Playwright base image
# already reserves 1000 for pwuser; rename that account instead of creating 1002.
RUN if id -u pwuser >/dev/null 2>&1; then \
      usermod -l appuser pwuser && groupmod -n appuser pwuser && \
      usermod -d /home/appuser -m appuser; \
    else \
      groupadd --gid 1000 appuser && \
      useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash appuser; \
    fi && \
    mkdir -p /app/evidence && \
    chown -R appuser:appuser /app
ENV HOME=/home/appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "nightwalker.api:app", "--host", "0.0.0.0", "--port", "8000"]
