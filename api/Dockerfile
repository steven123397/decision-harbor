FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
COPY pyproject.toml ./
COPY app ./app
COPY tests ./tests
COPY alembic.ini ./
COPY alembic ./alembic
COPY entrypoint.sh ./
RUN pip install --upgrade pip && pip install -e ".[test]" && chmod +x entrypoint.sh
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
EXPOSE 8000
