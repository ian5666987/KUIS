# FastAPI data plane — KWIC, frequency, n-gram, collocation, error
# analytics. Architecture plan §7. Same internal port number as
# django.Dockerfile (9415) is fine — each runs in its own container,
# resolved by container name on the shared network, not by port.

FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 9415

CMD ["uvicorn", "dataplane.main:app", "--host", "0.0.0.0", "--port", "9415", "--workers", "4"]
