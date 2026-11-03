FROM mcr.microsoft.com/playwright/python:v1.64.1-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY nightwalker ./nightwalker
RUN pip install --no-cache-dir .

RUN useradd --create-home --shell /bin/bash appuser
RUN mkdir -p /app/evidence && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "nightwalker.api:app", "--host", "0.0.0.0", "--port", "8000"]
