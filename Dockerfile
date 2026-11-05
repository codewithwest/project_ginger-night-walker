# Use uv for dependency resolution and installation; keep the Playwright
# browser/runtime image pinned to the matching Python package version.
FROM ghcr.io/astral-sh/uv:0.10.0 AS uv

FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app
# Copy the repository as a unit so uv.lock is included when it exists,
# while a fresh checkout can still bootstrap before the first lock is committed.
COPY . .

# Use the Python interpreter supplied by the Playwright base image. This keeps
# the venv interpreter independent of uv's root-owned managed Python installs.
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
