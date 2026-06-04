#!/usr/bin/env bash
# ── Hermes Agent Hardened Entry Point ─────────────────────────────────────
# Security improvements (Opción B):
#   - AUTO_UPDATE forced to false by default
#   - Explicit startup logging
#   - Fail-fast on critical errors
set -euo pipefail

AUTO_UPDATE="${AUTO_UPDATE:-false}"
LOGFILE="/root/.hermes/logs/entrypoint.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOGFILE"
}

log "══════════ Hermes Entry Point (Hardened B) ══════════"
log "AUTO_UPDATE is set to: ${AUTO_UPDATE}"

# ── Conditional auto-update ──────────────────────────────────────────────
if [ "$AUTO_UPDATE" = "true" ]; then
    log "WARNING: Auto-update is ENABLED — supply-chain risk accepted."
    cd /opt/hermes-agent
    if git pull --recurse-submodules 2>&1 | grep -v 'Already up to date'; then
        log "Hermes updated from upstream."
        export VIRTUAL_ENV=/opt/hermes-agent/venv
        uv pip install -e ".[all]" --quiet
        log "Dependencies refreshed."
    else
        log "Already up to date."
    fi
else
    log "Auto-update DISABLED (recommended). Running pinned version."
fi

# ── Apply tool event logging patch ─────────────────────────────────────────
log "Applying tool event logging patches..."
python3 -c "
import os, sys
try:
    import agent.tool_executor
    path = agent.tool_executor.__file__
    with open(path, 'r') as f:
        code = f.read()
    
    old_start = '        start = time.time()\\n        try:\\n            result = agent._invoke_tool('
    new_start = '''        start = time.time()
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
            result = agent._invoke_tool('''
            
    old_complete = '        duration = time.time() - start\\n        is_error, _ = _detect_tool_failure(function_name, result)'
    new_complete = '''        duration = time.time() - start
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
        is_error, _ = _detect_tool_failure(function_name, result)'''
        
    if old_start in code and old_complete in code:
        code = code.replace(old_start, new_start).replace(old_complete, new_complete)
        with open(path, 'w') as f:
            f.write(code)
        print('-> Successfully patched tool_executor.py!')
    else:
        print('-> Skip patching (signatures already patched or modified).')
except Exception as e:
    print('-> Error during dynamic patching: ' + str(e))
"

# ── Start dashboard ──────────────────────────────────────────────────────
log "Starting Hermes Dashboard on http://127.0.0.1:9119 …"
hermes dashboard --host 127.0.0.1 --port 9119 --no-open &
DASHBOARD_PID=$!
log "Dashboard PID: ${DASHBOARD_PID}"

# ── Verify gateway binary ────────────────────────────────────────────────
if ! command -v hermes &>/dev/null; then
    log "FATAL: 'hermes' CLI not found in PATH"
    exit 1
fi
if [ ! -f /auth_proxy.py ]; then
    log "FATAL: /auth_proxy.py not found"
    exit 1
fi

log "Starting hardened auth proxy on port ${PORT:-8080} …"
exec python3 /auth_proxy.py
