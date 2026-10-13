# teglab - single image: Angular UI built with Node, served by the FastAPI backend.

# ---- 1. build the Angular app ------------------------------------------------------
FROM node:24-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --no-audit --no-fund; else npm install --no-audit --no-fund; fi
COPY frontend/ ./
RUN npm run build

# ---- 2. Python runtime ----------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TEGLAB_DB=/data/teglab.db \
    TEGLAB_STATIC=/app/ui
WORKDIR /app
COPY backend/pyproject.toml backend/README.md ./backend/
COPY backend/src ./backend/src
RUN pip install --no-cache-dir ./backend
COPY --from=ui /ui/dist/teglab-ui/browser /app/ui
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "teglab.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
