# Multistage build — Node compiles the SPA, Python serves it.
#
# Stage 1: build the Vite bundle into frontend/dist.
# Stage 2: install Python deps + copy the dist into the image.
#          uvicorn serves both /api/* and the SPA on a single port.

# ── Stage 1 — frontend bundle ────────────────────────────────────────
FROM node:22-alpine AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build

# ── Stage 2 — Python runtime ─────────────────────────────────────────
FROM python:3.12-slim AS runtime

# System deps for PyMuPDF + OpenCV runtime. Slim base lacks libGL etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps in two passes — root requirements first (heavy:
# PyMuPDF, anthropic, opencv) then the lighter backend ones. Caches
# better when only one set changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# App source.
COPY ai/      ./ai/
COPY core/    ./core/
COPY parser/  ./parser/
COPY cv/      ./cv/
COPY backend/ ./backend/
COPY config.yaml ./

# Built SPA from stage 1.
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Non-root user (matches Fly + Render defaults).
RUN useradd --create-home --shell /bin/bash qto && chown -R qto:qto /app
USER qto

EXPOSE 8765

# Run alembic upgrade head before serving so the DB schema matches the
# code on every deploy. Falls back gracefully if DATABASE_URL points
# at a Supabase pooled connection (Alembic uses a fresh psycopg client).
CMD ["sh", "-c", "alembic -c backend/alembic.ini upgrade head && uvicorn backend.main:app --host 0.0.0.0 --port 8765 --workers 1"]
