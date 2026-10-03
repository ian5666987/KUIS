# Django back office — auth/RBAC, users, corpus/file metadata, admin.
# Architecture plan §7. Internal port 9415, matching the house convention
# (see identity-service-internal's Dockerfile) — nginx reaches this
# container by name on the shared `app-shared` network, never via a
# published host port.

FROM python:3.14-slim

WORKDIR /app

# psycopg2-binary needs libpq at runtime; build-essential covers any source
# wheels that don't ship a prebuilt one for this platform.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

COPY . .

EXPOSE 9415

# collectstatic and migrate both need real settings (SECRET_KEY, DB_*), which
# only exist at container runtime via env_file — not at build time, since the
# .env file is deliberately not copied into the image. Running them here
# instead of as a RUN step is slightly slower to start but avoids baking
# placeholder secrets into the image just to satisfy django-environ at build.
CMD ["sh", "-c", "python manage.py collectstatic --noinput && python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:9415"]
