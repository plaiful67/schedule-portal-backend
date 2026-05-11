# schedule-portal-backend Docker image.
#
# Pinned to python:3.13-slim — Python 3.14 doesn't yet have wheels for
# pydantic-core / Pillow on linux/amd64 (Cloud Run default arch), so we use
# 3.13 which has full wheel coverage. The local dev venv uses 3.14 because
# that's what's installed via homebrew on the operator's MBA; behavior is
# identical for our purposes (FastAPI + WeasyPrint + qrcode).
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# WeasyPrint needs Pango, Cairo, gdk-pixbuf, libffi. The skill templates
# @import Inter + Source Serif 4 from Google Fonts; WeasyPrint fetches them
# at render time over Cloud Run's outbound internet. DejaVu is the fallback.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
      libffi-dev shared-mime-info fonts-dejavu-core ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first (better layer caching).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Vendor the skills *and* the app + data + templates. The dev-mode "live"
# read from ~/.claude/skills/ falls back to vendor/ when that path doesn't
# exist — which is exactly the container's situation.
COPY vendor/   ./vendor/
COPY app/      ./app/
COPY data/     ./data/

ENV PORT=8080
EXPOSE 8080

# Cloud Run injects $PORT. Two workers, concurrency=4 per worker (set on
# the gcloud run deploy side); WeasyPrint is CPU-bound and blocks the
# event loop while rendering, so workers > 1 helps under contention.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2 --log-level info"]
