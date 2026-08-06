FROM python:3.13.14-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.29@sha256:eb2843a1e56fd9e30c7276ce1a52cba86e64c7b385f5e3279a0e08e02dd058fc /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.13.14-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

ARG APP_UID=10001
ARG APP_GID=10001

RUN groupadd --system --gid "${APP_GID}" bot \
    && useradd --system --uid "${APP_UID}" --gid bot --home-dir /app --shell /usr/sbin/nologin bot \
    && mkdir -p /app/data /app/storage /tmp/noadick \
    && chown -R bot:bot /app /tmp/noadick

COPY --from=builder /opt/venv /opt/venv
COPY . .

ENV PATH=/opt/venv/bin:$PATH \
    DB_PATH=/app/data/bot.db \
    STORAGE_PATH=/app/storage \
    BACKUP_DIR=/app/data/backups \
    HEARTBEAT_PATH=/tmp/noadick/heartbeat \
    TZ=Europe/Moscow \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER bot:bot

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "-m", "scripts.healthcheck"]

CMD ["python", "bot.py"]
