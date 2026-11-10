FROM node:22-bookworm-slim AS steel-mcp-build
WORKDIR /app/steel-mcp-server
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN git init && git remote add origin https://github.com/steel-dev/steel-mcp-server.git && git fetch --depth 1 origin f28a17571b10053e3df5e4bbbc4d3bc30f58ba39 && git checkout FETCH_HEAD
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
RUN useradd --create-home --shell /bin/bash appuser && \
    mkdir -p /app/evidence && \
    chown -R appuser:appuser /app
ENV HOME=/home/appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "nightwalker.api:app", "--host", "0.0.0.0", "--port", "8000"]
