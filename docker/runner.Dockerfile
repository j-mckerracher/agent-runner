# Agent Runner — Docker base image for container-mode evaluation
#
# Build:
#   docker build -f docker/runner.Dockerfile -t agent-runner:latest .
#
# NOTE — Claude Code CLI installation (Phase D.1):
#   The Claude Code CLI (npm package @anthropic-ai/claude-code) is NOT
#   installed in this Dockerfile. It is expected to be either:
#   (a) Pre-installed out-of-band in a downstream derived image, OR
#   (b) Mounted from the host at /usr/local/bin/claude via a Docker volume:
#         docker run -v /usr/local/bin/claude:/usr/local/bin/claude:ro ...
#   Phase D.1 will wire the official installation path once the Claude Code
#   CLI distribution channel is confirmed.

FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash --uid 1000 runner

# Copy packages into workspace
WORKDIR /workspace
COPY packages/ /workspace/packages/
COPY pyproject.toml /workspace/pyproject.toml
COPY bin/ /workspace/bin/

# Install the workspace in editable mode
RUN pip install --no-cache-dir -e /workspace && \
    pip install --no-cache-dir \
        pydantic>=2.0 \
        pyyaml>=6.0 \
        jsonschema>=4.0 \
        httpx>=0.27 \
        aiohttp>=3.9

# Copy entrypoint
COPY docker/entrypoint.sh /workspace/entrypoint.sh
RUN chmod +x /workspace/entrypoint.sh

# Switch to non-root user
USER runner

# Default entrypoint: invoke the headless runner
ENTRYPOINT ["/workspace/entrypoint.sh"]
