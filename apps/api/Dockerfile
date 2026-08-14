FROM python:3.13-slim

WORKDIR /app

COPY apps/api/pyproject.toml /app/apps/api/pyproject.toml
COPY apps/api/src /app/apps/api/src

RUN pip install --no-cache-dir "/app/apps/api[test]"

COPY apps/api/migrations /app/apps/api/migrations
COPY apps/api/tests /app/apps/api/tests
COPY datasets/sales-analytics-v1 /app/datasets/sales-analytics-v1

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV DECISIONHARBOR_APP_ROOT=/app/apps/api

WORKDIR /app/apps/api
