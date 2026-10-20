#!/usr/bin/env bash
set -eo pipefail

echo "=========================================================="
echo " ⚕  HERMES AGENT - HARDENED PRODUCTION ENVIRONMENT         "
echo "=========================================================="
echo "-> Starting gateway initialization..."

# Force disable auto-update features (Ader Standard)
export AUTO_UPDATE=false
export HERMES_NO_AUTO_UPDATE=1
echo "-> Configuration enforced: AUTO_UPDATE=false, HERMES_NO_AUTO_UPDATE=1"

# Define explicitly HERMES_HOME
export HERMES_HOME="/root/.hermes"
export PATH="/root/.local/bin:/usr/local/bin:$PATH"
echo "-> Enforcing HERMES_HOME=$HERMES_HOME"

# Create persistent directories inside HERMES_HOME to support mounted Railway volumes
echo "-> Verifying directory structure in $HERMES_HOME..."
mkdir -p "$HERMES_HOME"/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache,profiles}
if [ ! -f "$HERMES_HOME/.env" ]; then
    echo "-> Creating empty .env file..."
    touch "$HERMES_HOME/.env"
fi

# Restore Hermes runtime symlinks into $HERMES_HOME when a Railway volume is mounted over /root/.hermes
echo "-> Linking Hermes runtime binaries from /opt/hermes-runtime into $HERMES_HOME..."
for runtime_dir in hermes-agent installs tools; do
    if [ -d "/opt/hermes-runtime/$runtime_dir" ]; then
        rm -rf "$HERMES_HOME/$runtime_dir"
        ln -sfn "/opt/hermes-runtime/$runtime_dir" "$HERMES_HOME/$runtime_dir"
    fi
done

if [ ! -x "/root/.local/bin/hermes" ] && [ -x "$HERMES_HOME/hermes-agent/.hermes/bin/hermes" ]; then
    mkdir -p /root/.local/bin
    cat << 'EOF' > /root/.local/bin/hermes
#!/bin/sh
exec /root/.hermes/hermes-agent/.hermes/bin/hermes "$@"
EOF
    chmod +x /root/.local/bin/hermes
fi

# Apply XANA OS tool event logging patch to tool_executor.py
echo "-> Applying XANA OS tool event logging patches..."
python3 - << 'PYEOF' || true
import os
path = "/opt/hermes-runtime/hermes-agent/agent/tool_executor.py"
if not os.path.exists(path):
    path = "/root/.hermes/hermes-agent/agent/tool_executor.py"
if os.path.exists(path):
    with open(path, "r") as f:
        code = f.read()
    old_start = "        start = time.time()\n        try:\n            result = agent._invoke_tool("
    new_start = """        start = time.time()
        try:
            import requests
            requests.post('http://127.0.0.1:8080/api/internal/tool_event', json={
                'event': 'start',
                'tool': function_name,
                'args': function_args
            }, timeout=0.5)
        except Exception:
            pass
        try:
            result = agent._invoke_tool("""
    old_complete = "        duration = time.time() - start\n        is_error, _ = _detect_tool_failure(function_name, result)"
    new_complete = """        duration = time.time() - start
        try:
            import requests
            requests.post('http://127.0.0.1:8080/api/internal/tool_event', json={
                'event': 'complete',
                'tool': function_name,
                'duration': duration,
                'is_error': is_error if 'is_error' in locals() else False
            }, timeout=0.5)
        except Exception:
            pass
        is_error, _ = _detect_tool_failure(function_name, result)"""
    if old_start in code and old_complete in code:
        code = code.replace(old_start, new_start).replace(old_complete, new_complete)
        with open(path, "w") as f:
            f.write(code)
        print("-> Successfully patched tool_executor.py!")
    else:
        print("-> Skip patching (signatures already patched or modified).")
PYEOF

# Signal handling for container graceful shutdown
cleanup() {
    echo "-> Caught signal! Initiating graceful shutdown sequence..."
    if [ -n "${PROXY_PID:-}" ]; then
        echo "--> Terminating Auth Proxy (PID: $PROXY_PID)..."
        kill -TERM "$PROXY_PID" 2>/dev/null || true
    fi
    if [ -n "${DASHBOARD_PID:-}" ]; then
        echo "--> Terminating Hermes Dashboard (PID: $DASHBOARD_PID)..."
        kill -TERM "$DASHBOARD_PID" 2>/dev/null || true
    fi
    if [ -n "${GATEWAY_PID:-}" ]; then
        echo "--> Terminating Hermes Gateway (PID: $GATEWAY_PID)..."
        kill -TERM "$GATEWAY_PID" 2>/dev/null || true
    fi
    wait
    echo "-> Graceful shutdown complete. Exiting."
    exit 0
}

trap cleanup SIGTERM SIGINT

# 1. Start the Flask auth proxy first
echo "-> Launching Hardened Auth Proxy (Port ${PORT:-8080})..."
python3 /auth_proxy.py &
PROXY_PID=$!

sleep 2

if ! kill -0 "$PROXY_PID" 2>/dev/null; then
    echo "ERROR: Hardened Auth Proxy failed to start!" >&2
    exit 1
fi
echo "-> Hardened Auth Proxy successfully running (PID: $PROXY_PID)."

# 2. Start the Hermes Dashboard (internal port 9119)
echo "-> Launching Internal Hermes Dashboard (127.0.0.1:9119)..."
hermes dashboard --host 127.0.0.1 --port 9119 --no-open &
DASHBOARD_PID=$!

# Wait up to 20s for Dashboard to bind port 9119 while ensuring process stays alive
for i in $(seq 1 20); do
    if ! kill -0 "$DASHBOARD_PID" 2>/dev/null; then
        echo "ERROR: Hermes Dashboard failed to start!" >&2
        exit 1
    fi
    if python3 -c "import socket; s=socket.create_connection(('127.0.0.1', 9119), 0.5); s.close()" 2>/dev/null; then
        break
    fi
    sleep 1
done

echo "-> Hermes Dashboard successfully running (PID: $DASHBOARD_PID)."

# 3. Start the Hermes Gateway daemon
echo "-> Launching Hermes Gateway daemon..."
hermes gateway run &
GATEWAY_PID=$!

sleep 2

if ! kill -0 "$GATEWAY_PID" 2>/dev/null; then
    echo "ERROR: Hermes Gateway failed to start!" >&2
    exit 1
fi
echo "-> Hermes Gateway successfully running (PID: $GATEWAY_PID)."

echo "=========================================================="
echo " SYSTEM STATUS: ALL SECURE TUNNELS ENGAGED & OPERATIONAL   "
echo "=========================================================="

# Wait for background gateway process to manage container runtime
wait "$GATEWAY_PID"
