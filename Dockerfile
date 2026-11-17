FROM node:20-alpine AS web-builder
WORKDIR /build/web

COPY web/package.json web/package-lock.json ./
RUN npm ci || (for p in http://http.docker.internal:3128 http://host.docker.internal:10808 http://host.docker.internal:7897; do \
      HTTP_PROXY="$p" HTTPS_PROXY="$p" npm ci && exit 0; \
    done; exit 1)

COPY web/ ./
RUN npm run build


FROM python:3.11-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=18200 \
    WEB_DIST_DIR=/app/web/dist

WORKDIR /app/backend

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt || (for p in http://http.docker.internal:3128 http://host.docker.internal:10808 http://host.docker.internal:7897; do \
      pip install --no-cache-dir --proxy "$p" -r requirements.txt && exit 0; \
    done; exit 1)

COPY backend/ ./
COPY --from=web-builder /build/web/dist /app/web/dist

EXPOSE 18200

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18200/api/v1/health', timeout=3)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "18200"]
