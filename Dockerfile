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

# WeasyPrint needs Pango, Cairo, gdk-pixbuf, libffi.
#
# Fonts: skill templates @import Inter + Source Serif 4 from Google Fonts.
# WeasyPrint won't reliably fetch web fonts at render time (Cloud Run
# outbound + Pango font-matching quirks), so we bake the families into
# the image and let the @font-face declarations resolve locally:
#   - fonts-inter         (Debian bookworm; Inter variable font)
#   - fonts-dejavu-core   (universal fallback for any remaining glyphs)
#   - fonts-symbola       (monochrome emoji + Unicode-symbol fallback —
#                          covers ⚠️ 💊 📞 🍽️ etc. so they print crisply
#                          on B&W clinic laser printers)
#   - Source Serif 4      (downloaded from google/fonts repo at build time)
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
      libffi-dev shared-mime-info fonts-dejavu-core fonts-inter fonts-symbola \
      fontconfig ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# Source Serif 4 (the "color"/personalized templates' serif) plus the Calm
# theme fonts — Newsreader (serif) + Hanken Grotesk (sans). All variable,
# OFL-licensed, pulled from google/fonts and pinned to a commit so builds are
# reproducible. The Calm scheduler PDFs reference these by family name; baking
# them in keeps render-time output deterministic (no Google-Fonts fetch — the
# @import is stripped from the vendored calm CSS by the bowel_prep adapter).
ARG GOOGLE_FONTS_SHA=main
RUN mkdir -p /usr/share/fonts/truetype/sourceserif4 \
              /usr/share/fonts/truetype/newsreader \
              /usr/share/fonts/truetype/hankengrotesk \
 && curl -fsSL -o /usr/share/fonts/truetype/sourceserif4/SourceSerif4-Roman.ttf \
      "https://github.com/google/fonts/raw/${GOOGLE_FONTS_SHA}/ofl/sourceserif4/SourceSerif4%5Bopsz%2Cwght%5D.ttf" \
 && curl -fsSL -o /usr/share/fonts/truetype/sourceserif4/SourceSerif4-Italic.ttf \
      "https://github.com/google/fonts/raw/${GOOGLE_FONTS_SHA}/ofl/sourceserif4/SourceSerif4-Italic%5Bopsz%2Cwght%5D.ttf" \
 && curl -fsSL -o /usr/share/fonts/truetype/newsreader/Newsreader-Roman.ttf \
      "https://github.com/google/fonts/raw/${GOOGLE_FONTS_SHA}/ofl/newsreader/Newsreader%5Bopsz%2Cwght%5D.ttf" \
 && curl -fsSL -o /usr/share/fonts/truetype/newsreader/Newsreader-Italic.ttf \
      "https://github.com/google/fonts/raw/${GOOGLE_FONTS_SHA}/ofl/newsreader/Newsreader-Italic%5Bopsz%2Cwght%5D.ttf" \
 && curl -fsSL -o /usr/share/fonts/truetype/hankengrotesk/HankenGrotesk.ttf \
      "https://github.com/google/fonts/raw/${GOOGLE_FONTS_SHA}/ofl/hankengrotesk/HankenGrotesk%5Bwght%5D.ttf" \
 && fc-cache -fv >/dev/null \
 && fc-list | grep -iE "inter|source serif|newsreader|hanken" | head -10

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
