FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8080 \
    AUTO_UPDATE=false \
    HERMES_NO_AUTO_UPDATE=1 \
    HERMES_HOME=/root/.hermes \
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

# Install Node.js 22 (LTS) - Needed for Hermes Agent browser & web dashboard features
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages required for the hardened proxy
RUN pip install --no-cache-dir \
    flask \
    requests \
    gevent \
    gevent-websocket \
    websocket-client \
    pyyaml

# Install Hermes Agent using the official script in non-interactive mode
RUN curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash -s -- --skip-setup --skip-browser

# Relocate Hermes runtime directories outside /root/.hermes so mounting a persistent
# Railway volume onto /root/.hermes at container startup never masks the binary or venv.
RUN mkdir -p /opt/hermes-runtime \
    && for dir in hermes-agent installs tools; do \
         if [ -e "/root/.hermes/$dir" ]; then \
           mv "/root/.hermes/$dir" "/opt/hermes-runtime/$dir" && \
           ln -s "/opt/hermes-runtime/$dir" "/root/.hermes/$dir"; \
         fi; \
       done

# Ensure required persistent directories are created in default image layer
RUN mkdir -p /root/.hermes/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache,profiles} \
    && touch /root/.hermes/.env

# Copy application files
COPY auth_proxy.py /auth_proxy.py
COPY entrypoint.sh /entrypoint.sh
COPY Dockerfile /Dockerfile

# Ensure entrypoint.sh is executable
RUN chmod +x /entrypoint.sh && chmod 644 /auth_proxy.py

# Set active working directory
WORKDIR /root

# Expose public proxy port
EXPOSE 8080

# Define entrypoint
ENTRYPOINT ["/entrypoint.sh"]
