# ── Hermes Agent Hardened Dockerfile (Opción B) ──────────────────────────
# Security improvements:
#   - Pinned Python 3.11 for reproducibility
#   - --no-install-recommends to reduce attack surface
#   - Cloned from official NousResearch/hermes-agent (single source of truth)
#   - No auto-update by default (controlled via AUTO_UPDATE env var)
#   - Working directory isolation

FROM python:3.11-slim

# ── System dependencies ──────────────────────────────────────────────────
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
    git curl ca-certificates ripgrep ffmpeg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# ── UV package manager ───────────────────────────────────────────────────
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# ── Hermes installation (from official source) ───────────────────────────
RUN git clone --recurse-submodules \
    https://github.com/NousResearch/hermes-agent.git \
    /opt/hermes-agent

WORKDIR /opt/hermes-agent
RUN uv venv venv --python 3.11 \
    && VIRTUAL_ENV=/opt/hermes-agent/venv uv pip install -e ".[all]"

ENV PATH="/opt/hermes-agent/venv/bin:$PATH"

# ── Prepare persistent directories ────────────────────────────────────────
RUN mkdir -p /root/.hermes/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache} \
    && if [ -f cli-config.yaml.example ]; then \
    cp cli-config.yaml.example /root/.hermes/config.yaml; \
    fi \
    && touch /root/.hermes/.env

# ── Copy our hardened files ──────────────────────────────────────────────
COPY auth_proxy.py /auth_proxy.py
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && chmod 644 /auth_proxy.py

# ── Runtime ──────────────────────────────────────────────────────────────
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
