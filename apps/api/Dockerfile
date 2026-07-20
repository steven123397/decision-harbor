FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/apps/api/src

WORKDIR /app/apps/api

COPY apps/api/pyproject.toml ./
RUN mkdir -p src && pip install --no-cache-dir ".[test]"

COPY apps/api/src ./src
COPY apps/api/alembic-*.ini ./
COPY apps/api/migrations ./migrations
COPY apps/api/tests ./tests
COPY datasets /app/datasets

EXPOSE 8000

CMD ["uvicorn", "decisionharbor.api:create_runtime_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
