# ── Stage 1: build ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Build-time deps for C-extension wheels (lxml, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
        libxml2-dev \
        libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt

# Pre-download the Camoufox Firefox binary at build time (~120 MB).
# This prevents a slow runtime download on first request.
# camoufox stores the binary in user_cache_dir("camoufox") = /root/.cache/camoufox
RUN PYTHONPATH=/install/lib/python3.12/site-packages \
    python -m camoufox fetch


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# ── System dependencies for Firefox / Camoufox ────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        # Virtual framebuffer — required for BROWSER_HEADLESS=virtual on Linux
        xvfb \
        # Firefox runtime libraries
        libgtk-3-0 \
        libdbus-glib-1-2 \
        libxt6 \
        libasound2 \
        libx11-xcb1 \
        libxcb-dri3-0 \
        libdrm2 \
        libgbm1 \
        libxcomposite1 \
        libxcursor1 \
        libxdamage1 \
        libxfixes3 \
        libxi6 \
        libxrandr2 \
        libxrender1 \
        libxss1 \
        libxtst6 \
        # Fonts so pages render correctly
        fonts-liberation \
        fontconfig \
        # Misc
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# ── Python packages from builder ──────────────────────────────────────────────
COPY --from=builder /install /usr/local

# ── Camoufox browser binary from builder ──────────────────────────────────────
# platformdirs resolves to /root/.cache/camoufox when running as root
COPY --from=builder /root/.cache/camoufox /root/.cache/camoufox

# ── Application source ────────────────────────────────────────────────────────
COPY app/ ./app/

# ── Environment defaults ──────────────────────────────────────────────────────
# Clever Cloud exposes port 8080 by default for Docker apps.
# All values can be overridden via Clever Cloud environment variables.
ENV APP_HOST=0.0.0.0 \
    APP_PORT=8080 \
    APP_RELOAD=false \
    LOG_LEVEL=info \
    # "virtual" runs Camoufox through Xvfb — required on headless Linux servers
    BROWSER_HEADLESS=virtual \
    BROWSER_HUMANIZE=true \
    BROWSER_GEOIP=true \
    BROWSER_OS=windows,macos,linux \
    BROWSER_LOCALE=en-US \
    BROWSER_BLOCK_WEBRTC=true \
    BROWSER_BLOCK_IMAGES=false \
    BROWSER_PROXY= \
    BROWSER_MAX_CONCURRENCY=4 \
    BROWSER_NAV_TIMEOUT_MS=45000

EXPOSE 8080

# Single worker — Camoufox is process-global; use BROWSER_MAX_CONCURRENCY
# to control parallelism within the process.
CMD uvicorn app.main:app \
        --host "$APP_HOST" \
        --port "$APP_PORT" \
        --workers 1 \
        --loop uvloop \
        --no-access-log
