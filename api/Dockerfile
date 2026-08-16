FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY api/pyproject.toml ./
# 先装依赖再拷源码，保证依赖层可缓存。
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.32" \
    "pydantic>=2.9" "pydantic-settings>=2.6" "sqlalchemy>=2.0" \
    "alembic>=1.14" "psycopg[binary]>=3.2" "sqlglot>=26.0" \
    "pytest>=8.3" "httpx>=0.28"

COPY api/app ./app
COPY api/alembic.ini ./
COPY api/migrations ./migrations
COPY api/tests ./tests
# 数据集是产品输入，烤进镜像而非绑定挂载（并行工作区不共享主机目录）。
COPY datasets /srv/datasets

EXPOSE 8000

# 服务进程只持有运行时低权限身份（由 compose 注入）；
# 高权限迁移/seed 由独立 init 服务以本镜像执行 app.bootstrap 完成。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=12 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1
