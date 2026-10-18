# syntax=docker/dockerfile:1

# --- Frontend shell (React + TS + Vite) ---
FROM node:22-alpine AS web
WORKDIR /src
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/tsconfig.json web/vite.config.ts web/index.html ./
COPY web/src ./src
RUN npm run build

# --- Kernel (mihomo), pinned release per architecture ---
FROM alpine:3.22 AS kernel
ARG MIHOMO_VERSION=v1.19.32
ARG TARGETARCH
RUN apk add --no-cache curl \
 && case "$TARGETARCH" in \
      amd64) arch=amd64 ;; \
      arm64) arch=arm64 ;; \
      arm)   arch=armv7 ;; \
      *) echo "unsupported TARGETARCH: $TARGETARCH" >&2; exit 1 ;; \
    esac \
 && curl -sSfL \
      "https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/mihomo-linux-${arch}-${MIHOMO_VERSION}.gz" \
      -o /tmp/mihomo.gz \
 && gunzip /tmp/mihomo.gz \
 && chmod +x /tmp/mihomo \
 && /tmp/mihomo -v

# --- Runtime: tini (PID 1) + Management Panel + Kernel ---
FROM python:3.14-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY clashflow ./clashflow
RUN pip install --no-cache-dir .
COPY --from=kernel /tmp/mihomo /usr/local/bin/mihomo
COPY --from=web /src/dist /app/web/dist

ENV CLASHFLOW_DATA_DIR=/data \
    CLASHFLOW_WEB_DIST=/app/web/dist
VOLUME /data
EXPOSE 7890 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/service/health', timeout=4)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "clashflow"]
