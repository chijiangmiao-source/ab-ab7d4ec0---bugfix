# syntax=docker/dockerfile:1

# ---- builder: compile the native Steiner DP core ------------------------
FROM python:3.11-slim AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY core/steiner.c ./steiner.c
RUN gcc -O2 -std=c11 -Wall -Wextra -o /build/steiner steiner.c

# ---- runtime: stdlib-only Python API plus the compiled core -------------
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUDIT_HOST=0.0.0.0 \
    AUDIT_PORT=8080

# Non-root user.
RUN useradd --create-home --uid 10001 audit

WORKDIR /app
COPY --from=builder /build/steiner /app/core/steiner
COPY core/steiner.c /app/core/steiner.c
COPY app/ /app/app/
COPY tests/ /app/tests/

RUN chmod 0555 /app/core/steiner \
    && chown -R audit:audit /app

USER audit
EXPOSE 8080

# Liveness is checked by Compose against GET /healthz (see compose file).
CMD ["python", "-m", "app.server"]
