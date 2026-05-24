FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8080 \
    AUTO_UPDATE=false \
    HERMES_NO_AUTO_UPDATE=1 \
    PATH="/root/.local/bin:/usr/local/bin:$PATH"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    ca-certificates \
    ripgrep \
    ffmpeg \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22 (LTS) - Needed for Hermes Agent browser features
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages required for the hardened proxy
RUN pip install --no-cache-dir \
    flask \
    requests \
    gevent \
    gevent-websocket \
    websocket-client

# Install Hermes Agent using the official script in non-interactive mode
RUN curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash -s -- --skip-setup --skip-browser

# Ensure required directories are created
RUN mkdir -p /root/.hermes/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache} \
    && touch /root/.hermes/.env

# Copy application files
COPY auth_proxy.py /auth_proxy.py
COPY entrypoint.sh /entrypoint.sh
COPY Dockerfile /Dockerfile

# Ensure entrypoint.sh is executable
RUN chmod +x /entrypoint.sh

# Set active working directory
WORKDIR /root

# Define entrypoint
ENTRYPOINT ["/entrypoint.sh"]
