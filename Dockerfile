# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEMO_ENABLED=1 \
    CAREOPS_EMBEDDING_PROVIDER=local

WORKDIR /app

RUN useradd --create-home --uid 10001 careops

COPY pyproject.toml README.md LICENSE ./
COPY careops ./careops
COPY app ./app
COPY semantic ./semantic
COPY knowledge ./knowledge
COPY data ./data
COPY evals ./evals
COPY tests ./tests
COPY mcp_server ./mcp_server
COPY examples ./examples

RUN pip install --upgrade pip \
    && pip install -e ".[web,postgres,rag]" \
    && chown -R careops:careops /app

USER careops
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
