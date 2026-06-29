# Build context = repo root (so both web/ and api/ are available).
#   docker build -f api/Dockerfile -t event-demand-api .

# ---- Stage 1: build the React dashboard ----
# Debian-slim (glibc), not alpine/musl: avoids the npm optional-dependency bug that can
# drop the platform rollup/esbuild binary during `npm ci`.
FROM node:20-slim AS web
WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
# Production build -> same-origin API base (see web/src/api/client.ts).
RUN npm run build

# ---- Stage 2: FastAPI service + the built dashboard ----
FROM python:3.12-slim

WORKDIR /app

COPY api/requirements.txt /app/api/requirements.txt
RUN pip install --no-cache-dir -r /app/api/requirements.txt

COPY api/ /app/api/
COPY --from=web /web/dist /app/api/static

EXPOSE 8080

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8080"]
