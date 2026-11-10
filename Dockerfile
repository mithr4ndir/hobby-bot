# hobby-bot image: Python plus discord.py, installed from a hash-locked export.
#
# Base image is pinned by tag AND index digest. To bump it, pull the new tag
# and read the digest back:
#   docker pull python:3.12.14-slim-bookworm
#   docker inspect --format '{{index .RepoDigests 0}}' python:3.12.14-slim-bookworm

# Stage 1: build a venv from requirements.lock. Every wheel is verified against
# its sha256 (uv export --hash), so a tampered index cannot slip a package in.
FROM python:3.15.0-slim-bookworm@sha256:0e019bbdfb5d9874cb2389f1c2884b64aa2ac97c99960b5bc521cef978c0617d AS deps
WORKDIR /build
COPY requirements.lock ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --require-hashes --no-deps -r requirements.lock \
 && /opt/venv/bin/pip uninstall -y pip setuptools 2>/dev/null || true

# Stage 2: runtime.
FROM python:3.15.0-slim-bookworm@sha256:0e019bbdfb5d9874cb2389f1c2884b64aa2ac97c99960b5bc521cef978c0617d

# ca-certificates for TLS to Discord, tzdata so TZ=America/Los_Angeles resolves.
# Pending Debian security updates are applied at build time, and pip is removed:
# nothing is installed at run time.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && python -m pip uninstall -y pip

RUN groupadd -r -g 65532 nonroot \
 && useradd -r -u 65532 -g 65532 -d /tmp/home -s /usr/sbin/nologin nonroot \
 && mkdir -p /data \
 && chown 65532:65532 /data \
 && chmod 0700 /data

COPY --from=deps /opt/venv /opt/venv

WORKDIR /app
# Owned by root and read-only to the runtime user: the app never writes here.
# The bundled activities.json is only a seed; the live copy lives in /data.
COPY bot.py suggest.py feed_state.py paths.py runtime.py webhook.py healthcheck.py activities.json /app/
# Bytecode is compiled at build time; the root filesystem is read-only at run time.
RUN python -m compileall -q /app

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp/home \
    HOBBY_BOT_DATA_DIR=/data \
    HOBBY_BOT_HEARTBEAT=/tmp/heartbeat

USER 65532:65532

ENTRYPOINT ["python", "/app/bot.py"]
