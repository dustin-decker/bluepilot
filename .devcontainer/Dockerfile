FROM ghcr.io/astral-sh/uv:0.12.4 AS uv
FROM ubuntu:24.04
COPY --from=uv /uv /uvx /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ca-certificates curl git libcurl4-openssl-dev libegl1 libgl1 \
    libglib2.0-0 sudo && rm -rf /var/lib/apt/lists/*
ARG MISE_VERSION=v2026.2.11
RUN curl -fsSL https://mise.run | MISE_VERSION=${MISE_VERSION} MISE_INSTALL_PATH=/usr/local/bin/mise sh
RUN useradd -m -s /bin/bash -u 1001 developer && \
    mkdir -p /opt/venv /opt/uv-cache && chown -R developer:developer /opt/venv /opt/uv-cache
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_CACHE_DIR=/opt/uv-cache UV_LINK_MODE=copy
ENV PATH="/opt/venv/bin:/usr/local/bin:${PATH}" PYTHONUNBUFFERED=1 MPLBACKEND=Agg
USER developer
WORKDIR /workspaces/bluepilot
