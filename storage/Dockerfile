ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base:latest

# ── Stage 1: Build React frontend ──────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# ── Stage 2: Runtime ───────────────────────────────────────────────────────
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip nginx jq curl gettext jpeg-dev zlib-dev

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# React SPA
COPY --from=frontend-builder /app/dist /var/www/html

# nginx config template
COPY nginx.conf.template /etc/nginx/nginx.conf.template

# Addon manifest (version is read from here at runtime)
COPY config.json /config.json

# FastAPI app
COPY app/ /app/

# s6-overlay services
COPY rootfs /

WORKDIR /app
