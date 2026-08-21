# syntax=docker/dockerfile:1

# --- builder: resolve and build the wheel in a full image ------------------
FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels .

# --- runtime: install the wheel into a minimal, non-root image -------------
FROM python:3.12-slim AS runtime

# Stalcraft-related nicknames/text throughout the bot are Cyrillic; the
# locale keeps logging and Discord round-trips from mangling non-ASCII output.
ENV PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN groupadd --gid 1000 stalbot \
    && useradd --uid 1000 --gid stalbot --create-home --shell /usr/sbin/nologin stalbot

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

# `data/` (SQLite cache, OCR samples) is meant to be bind-mounted from the
# host — see docker-compose.yml.
RUN mkdir -p /app/data && chown -R stalbot:stalbot /app

USER stalbot

ENTRYPOINT ["python", "-m", "stalbot"]
