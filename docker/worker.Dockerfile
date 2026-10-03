# Celery worker — preprocessing, tokenization, statistics. Architecture plan
# §7. Pure Redis consumer, no inbound HTTP traffic, so no EXPOSE.

FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info", "--concurrency=4"]
