FROM python:3.13-slim

WORKDIR /app

COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY api /app
COPY datasets /datasets

ENV PYTHONUNBUFFERED=1
ENV DATASET_DIR=/datasets/sales-analytics-v1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
