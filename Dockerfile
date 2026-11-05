# Use uv for dependency resolution and installation; keep the Playwright
# browser/runtime image pinned to the matching Python package version.
FROM ghcr.io/astral-sh/uv:0.10.0 AS uv

FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
COPY pyproject.toml README.md ./
COPY nightwalker ./nightwalker

# Resolve and install the project and runtime dependencies without pip.
RUN uv sync --no-dev --no-editable

RUN useradd --create-home --shell /bin/bash appuser
RUN mkdir -p /app/evidence && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "nightwalker.api:app", "--host", "0.0.0.0", "--port", "8000"]
