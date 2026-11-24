# Jairbet Telegram bot. Build and run with: docker compose up -d --build
FROM python:3.12-slim

ARG APP_UID=1000
ARG APP_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    TZ=America/Sao_Paulo \
    NO_COLOR=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    DB_PATH=/data/jairbet.db \
    BACKUP_DIR=/data/backups

# tzdata: local time in logs; sqlite3: manual backups/restores inside the container.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first so code changes do not reinstall them.
COPY pyproject.toml ./
RUN python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb')); \
print('\n'.join(d['project']['dependencies']))" > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY jairbet ./jairbet
COPY db ./db

RUN groupadd --gid "$APP_GID" jairbet \
    && useradd --uid "$APP_UID" --gid "$APP_GID" --no-create-home \
       --shell /usr/sbin/nologin jairbet \
    && mkdir -p /data/backups \
    && chown -R jairbet:jairbet /data

USER jairbet
VOLUME ["/data"]

CMD ["python", "-m", "jairbet.bot"]
