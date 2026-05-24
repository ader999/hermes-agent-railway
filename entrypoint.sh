#!/usr/bin/env bash
set -eo pipefail

# Print clean, striking startup logs
echo "=========================================================="
echo " ⚕  HERMES AGENT - HARDENED PRODUCTION ENVIRONMENT         "
echo "=========================================================="
echo "-> Starting gateway initialization..."

# Force disable auto-update features as requested
export AUTO_UPDATE=false
export HERMES_NO_AUTO_UPDATE=1
echo "-> Configuration enforced: AUTO_UPDATE=false, HERMES_NO_AUTO_UPDATE=1"

# Define explicitly HERMES_HOME
export HERMES_HOME="/root/.hermes"
echo "-> Enforcing HERMES_HOME=$HERMES_HOME"

# Create directories inside HERMES_HOME if they don't exist to support empty persistent volumes
echo "-> Verifying directory structure in $HERMES_HOME..."
mkdir -p "$HERMES_HOME"/{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache}
if [ ! -f "$HERMES_HOME/.env" ]; then
    echo "-> Creating empty .env file..."
    touch "$HERMES_HOME/.env"
fi

# Signal handling for container graceful shutdown
cleanup() {
    echo "-> Caught signal! Initiating graceful shutdown sequence..."
    if [ -n "$PROXY_PID" ]; then
        echo "--> Terminating Auth Proxy (PID: $PROXY_PID)..."
        kill -TERM "$PROXY_PID" 2>/dev/null || true
    fi
    if [ -n "$DASHBOARD_PID" ]; then
        echo "--> Terminating Hermes Dashboard (PID: $DASHBOARD_PID)..."
        kill -TERM "$DASHBOARD_PID" 2>/dev/null || true
    fi
    if [ -n "$GATEWAY_PID" ]; then
        echo "--> Terminating Hermes Gateway (PID: $GATEWAY_PID)..."
        kill -TERM "$GATEWAY_PID" 2>/dev/null || true
    fi
    wait
    echo "-> Graceful shutdown complete. Exiting."
    exit 0
}

trap cleanup SIGTERM SIGINT

# 1. Start the Flask auth proxy first
echo "-> Launching Hardened Auth Proxy (Port 8080)..."
python /auth_proxy.py &
PROXY_PID=$!

# Brief pause to ensure auth proxy binds to port 8080
sleep 2

if ! kill -0 "$PROXY_PID" 2>/dev/null; then
    echo "ERROR: Hardened Auth Proxy failed to start!" >&2
    exit 1
fi
echo "-> Hardened Auth Proxy successfully running (PID: $PROXY_PID)."

# 2. Start the Hermes Dashboard (normally port 9119, internal only)
echo "-> Launching Internal Hermes Dashboard..."
hermes dashboard --host 127.0.0.1 --port 9119 --no-open &
DASHBOARD_PID=$!

sleep 2

if ! kill -0 "$DASHBOARD_PID" 2>/dev/null; then
    echo "ERROR: Hermes Dashboard failed to start!" >&2
    exit 1
fi
echo "-> Hermes Dashboard successfully running (PID: $DASHBOARD_PID)."

# 3. Start the Hermes Gateway
echo "-> Launching Hermes Gateway daemon..."
hermes gateway run &
GATEWAY_PID=$!

sleep 1

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
