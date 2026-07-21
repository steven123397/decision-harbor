FROM python:3.13-slim

WORKDIR /app

ARG http_proxy
ARG https_proxy

COPY pyproject.toml .
COPY app/ app/
COPY alembic/ alembic/

RUN pip install --no-cache-dir .

ENV DATASET_DIR=/datasets/sales-analytics-v1

EXPOSE 8000

CMD ["sh", "-c", "python -m app.migrate && python -m app.seed && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
