FROM python:3.12-slim AS base

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-editable

COPY . .

ENV PYTHONPATH=/app/src

FROM base AS init

ENV OLLAMA_BASE_URL=http://ollama:11434/v1
ENV INDEXING_EMBEDDING_BASE_URL=http://ollama:11434/v1
ENV LANCEDB_URI=/app/data/processed/lancedb

CMD ["/bin/sh", "-c", "echo 'Pulling Ollama models...' && curl -s http://ollama:11434/api/pull -d '{\"name\": \"qwen2.5:1.5b\"}' && curl -s http://ollama:11434/api/pull -d '{\"name\": \"nomic-embed-text\"}' && echo 'Running ingestion...' && python -m tesla_finrag ingest"]

FROM base AS app

ENV OLLAMA_BASE_URL=http://ollama:11434/v1
ENV LANCEDB_URI=/app/data/processed/lancedb

EXPOSE 8501

CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address", "0.0.0.0"]
