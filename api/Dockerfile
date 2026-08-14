FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY pyproject.toml ./
# 先装依赖再拷源码，保证依赖层可缓存。
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.32" \
    "pydantic>=2.9" "pydantic-settings>=2.6" "sqlalchemy>=2.0" \
    "alembic>=1.14" "psycopg[binary]>=3.2" "sqlglot>=26.0" \
    "pytest>=8.3" "httpx>=0.28"

COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
COPY tests ./tests
# compose 通过 env_file 提供数据库地址；dataset 以只读卷挂载进容器。
VOLUME ["/srv/datasets"]

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1

CMD ["python", "-m", "app.bootstrap"]
