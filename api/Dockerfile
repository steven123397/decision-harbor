FROM python:3.13-slim

# 依赖源可按网络环境覆盖（见 .env.example 的 PIP_INDEX_URL）
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app

# 依赖按 requirements.lock 的解析结果安装；--no-deps 保证不引入锁外版本，
# --no-build-isolation 让构建后端也取自锁文件，构建结果与网络时点无关。
COPY requirements.lock pyproject.toml ./
RUN mkdir -p app && touch app/__init__.py \
    && pip install --no-cache-dir --no-deps -r requirements.lock \
    && pip install --no-cache-dir --no-deps --no-build-isolation -e .

COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
