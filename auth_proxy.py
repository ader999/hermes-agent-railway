from gevent import monkey
monkey.patch_all()

import os
import sys
import time
import hmac
import hashlib
import secrets
import logging
import subprocess
from collections import defaultdict

import requests
import websocket
import gevent
from flask import Flask, request, Response, redirect, url_for, jsonify, render_template_string

# Setup basic logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("auth_proxy")

HERMES_HOME = "/root/.hermes"
UPSTREAM = os.environ.get("HERMES_UPSTREAM", "http://127.0.0.1:9119")
USERNAME = os.environ.get("DASHBOARD_USER", "admin")
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
COOKIE = "hermes_auth"
MAX_AGE = 7 * 86400

PUBLIC_PATHS = {
    "/api/status",
    "/api/health",
    "/auth/password-login",
    "/api/auth/ws-ticket",
}
PUBLIC_PREFIXES = (
    "/auth/native/",
)

# Derive a consistent HMAC key from PASSWORD to survive restarts
if not PASSWORD:
    logger.error("ERROR: DASHBOARD_PASSWORD must be set.")
    sys.exit(1)

SECRET_KEY = hashlib.sha256(PASSWORD.encode()).digest()

# Rate limiting settings
RATE_LIMIT = 30  # requests per minute
RATE_LIMIT_WINDOW = 60  # seconds
login_attempts = defaultdict(list)

def is_rate_limited(ip):
    now = time.time()
    login_attempts[ip] = [t for t in login_attempts[ip] if now - t < RATE_LIMIT_WINDOW]
    if len(login_attempts[ip]) >= RATE_LIMIT:
        return True
    login_attempts[ip].append(now)
    return False

def make_token():
    expires = str(int(time.time()) + MAX_AGE)
    sig = hmac.new(SECRET_KEY, expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"

def check_token(token):
    try:
        expires, sig = token.rsplit(".", 1)
        if int(expires) < time.time():
            return False
        expected = hmac.new(SECRET_KEY, expires.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected)
    except Exception:
        return False

def is_public_path(path_str):
    normalized = "/" + path_str.lstrip("/")
    if normalized in PUBLIC_PATHS:
        return True
    return any(normalized.startswith(prefix) for prefix in PUBLIC_PREFIXES)

def is_request_authenticated():
    token = request.cookies.get(COOKIE)
    if token and check_token(token):
        return True
    proxy_key = request.headers.get("X-Proxy-Key") or request.headers.get("X-Proxy-Token")
    if proxy_key and hmac.compare_digest(proxy_key, PASSWORD):
        return True
    return False

# Custom cyberpunk terminal styled login page
LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hermes Agent Console</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #03070c;
    --surface: rgba(10, 18, 30, 0.75);
    --border: rgba(0, 255, 204, 0.15);
    --border-focus: rgba(0, 255, 204, 0.7);
    --text: #00ffcc;
    --text-muted: #4e7370;
    --accent: #00ffcc;
    --accent-glow: rgba(0, 255, 204, 0.3);
    --error-bg: rgba(255, 0, 85, 0.1);
    --error-border: rgba(255, 0, 85, 0.4);
    --error-text: #ff0055;
    --terminal-output: #08161a;
  }
  body {
    font-family: 'JetBrains Mono', monospace;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    overflow: hidden;
  }
  body::before {
    content: '';
    position: fixed;
    inset: 0;
    background: 
      linear-gradient(rgba(18, 16, 16, 0) 50%, rgba(0, 0, 0, 0.25) 50%),
      linear-gradient(90deg, rgba(255, 0, 0, 0.04), rgba(0, 255, 0, 0.02), rgba(0, 0, 255, 0.04));
    background-size: 100% 4px, 6px 100%;
    z-index: 10;
    pointer-events: none;
  }
  body::after {
    content: '';
    position: fixed;
    inset: 0;
    background: radial-gradient(circle, transparent 20%, #03070c 80%);
    z-index: 2;
    pointer-events: none;
  }
  .grid-bg {
    position: fixed;
    inset: 0;
    background-image: 
      linear-gradient(to right, rgba(0, 255, 204, 0.02) 1px, transparent 1px),
      linear-gradient(to bottom, rgba(0, 255, 204, 0.02) 1px, transparent 1px);
    background-size: 40px 40px;
    z-index: 1;
    pointer-events: none;
  }
  @keyframes scanline {
    0% { transform: translateY(-100%); }
    100% { transform: translateY(100%); }
  }
  .scanline-animation {
    position: fixed;
    top: 0;
    left: 0;
    width: 100%;
    height: 10px;
    background: rgba(0, 255, 204, 0.03);
    box-shadow: 0 0 10px rgba(0, 255, 204, 0.15);
    z-index: 3;
    animation: scanline 8s linear infinite;
    pointer-events: none;
  }
  @keyframes blink {
    50% { opacity: 0; }
  }
  .cursor-blink {
    animation: blink 1s step-start infinite;
  }
  .login-wrapper {
    position: relative;
    z-index: 5;
    width: 100%;
    max-width: 440px;
    padding: 0 1.5rem;
  }
  .terminal-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 2rem;
    box-shadow: 0 0 35px rgba(0, 255, 204, 0.05), inset 0 0 15px rgba(0, 255, 204, 0.02);
    backdrop-filter: blur(12px);
    position: relative;
  }
  .terminal-card::before {
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 3px;
    background: linear-gradient(90deg, #00ffcc, #8a2be2);
  }
  .terminal-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px dashed var(--border);
    padding-bottom: 1rem;
    margin-bottom: 1.5rem;
  }
  .terminal-dots {
    display: flex;
    gap: 6px;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--border);
  }
  .dot.active {
    background: var(--accent);
    box-shadow: 0 0 6px var(--accent);
  }
  .terminal-title {
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
  }
  .brand {
    text-align: left;
    margin-bottom: 1.5rem;
  }
  .brand h1 {
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: 1px;
    margin-bottom: 0.2rem;
    text-shadow: 0 0 8px var(--accent-glow);
  }
  .brand p {
    font-size: 0.75rem;
    color: var(--text-muted);
  }
  .terminal-logs {
    background: var(--terminal-output);
    border-radius: 3px;
    padding: 0.75rem;
    font-size: 0.7rem;
    color: #83b6b0;
    margin-bottom: 1.5rem;
    border-left: 2px solid var(--accent);
    line-height: 1.4;
  }
  .log-line {
    margin-bottom: 2px;
    display: flex;
    gap: 8px;
  }
  .log-line span.ok {
    color: #00ffcc;
    font-weight: bold;
  }
  .field {
    margin-bottom: 1.25rem;
    position: relative;
  }
  label {
    display: block;
    font-size: 0.7rem;
    font-weight: 700;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 1.5px;
    margin-bottom: 0.5rem;
  }
  .input-wrapper {
    position: relative;
  }
  .input-wrapper::before {
    content: '>';
    position: absolute;
    left: 10px;
    top: 50%;
    transform: translateY(-50%);
    font-size: 0.9rem;
    color: var(--accent);
  }
  input {
    width: 100%;
    padding: 0.75rem 0.75rem 0.75rem 25px;
    background: rgba(0, 0, 0, 0.5);
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text);
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.85rem;
    outline: none;
    transition: all 0.3s ease;
  }
  input:focus {
    border-color: var(--border-focus);
    box-shadow: 0 0 10px rgba(0, 255, 204, 0.15), inset 0 0 5px rgba(0, 255, 204, 0.05);
  }
  button {
    width: 100%;
    padding: 0.8rem;
    background: transparent;
    color: var(--accent);
    border: 1px solid var(--accent);
    border-radius: 3px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.8rem;
    font-weight: 700;
    letter-spacing: 2px;
    text-transform: uppercase;
    cursor: pointer;
    transition: all 0.3s ease;
    position: relative;
    overflow: hidden;
  }
  button:hover {
    background: var(--accent);
    color: var(--bg);
    box-shadow: 0 0 15px rgba(0, 255, 204, 0.4);
  }
  button:active {
    transform: scale(0.98);
  }
  .error {
    background: var(--error-bg);
    border: 1px solid var(--error-border);
    color: var(--error-text);
    padding: 0.75rem;
    border-radius: 3px;
    font-size: 0.75rem;
    margin-bottom: 1.25rem;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .error::before {
    content: '[!]';
    font-weight: bold;
  }
</style>
</head>
<body>
<div class="grid-bg"></div>
<div class="scanline-animation"></div>
<div class="login-wrapper">
  <div class="terminal-card">
    <div class="terminal-header">
      <div class="terminal-dots">
        <div class="dot active"></div>
        <div class="dot"></div>
        <div class="dot"></div>
      </div>
      <div class="terminal-title">AUTH_GATE_V2.1</div>
    </div>
    
    <div class="brand">
      <h1>HERMES AGENT</h1>
      <p>HARDENED GATEWAY PROXY</p>
    </div>

    <div class="terminal-logs">
      <div class="log-line"><span class="ok">[OK]</span> SECURE_TUNNEL_ACTIVE</div>
      <div class="log-line"><span class="ok">[OK]</span> HMAC_SHA256_ACTIVE</div>
      <div class="log-line"><span class="ok">[OK]</span> RATE_LIMITER_ENGAGED</div>
    </div>

    {% if error %}
    <div class="error">
      {{ error }}
    </div>
    {% endif %}

    <form method="POST" action="/login">
      <div class="field">
        <label for="username">OPERATOR_ID</label>
        <div class="input-wrapper">
          <input id="username" name="username" type="text" autocomplete="username" required placeholder="admin">
        </div>
      </div>
      <div class="field">
        <label for="password">ACCESS_KEY</label>
        <div class="input-wrapper">
          <input id="password" name="password" type="password" autocomplete="current-password" required placeholder="••••••••">
        </div>
      </div>
      <button type="submit">AUTHENTICATE <span class="cursor-blink">_</span></button>
    </form>
  </div>
</div>
</body>
</html>"""

# Status Widget to inject in downstream web UI
GATEWAY_WIDGET = """
<div id="gw-widget" style="position:fixed;bottom:20px;right:20px;z-index:99999;
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;font-size:13px;">
  <div style="background:#111920;border:1px solid rgba(45,212,191,0.2);border-radius:10px;
    padding:12px 16px;display:flex;flex-direction:column;gap:8px;
    box-shadow:0 4px 20px rgba(0,0,0,0.4);min-width:180px;">
    <div style="display:flex;align-items:center;gap:10px;">
      <span id="gw-dot" style="width:8px;height:8px;border-radius:50%;background:#888;flex-shrink:0;"></span>
      <span id="gw-label" style="color:#7899aa;flex:1;">Gateway</span>
      <button id="gw-btn" onclick="gwRestart()" style="background:#2dd4bf;color:#0a0f14;border:none;
        border-radius:5px;padding:4px 12px;font-size:12px;font-weight:600;cursor:pointer;">Restart</button>
    </div>
    <div id="gw-vol" style="display:none;font-size:11px;padding-top:4px;border-top:1px solid rgba(45,212,191,0.1);"></div>
  </div>
</div>
<script>
function gwStatus(){
  fetch('/api/gateway/status').then(r=>r.json()).then(d=>{
    document.getElementById('gw-dot').style.background=d.running?'#4ade80':'#ef4444';
    document.getElementById('gw-label').textContent=d.running?'Gateway running':'Gateway stopped';
    var vol=document.getElementById('gw-vol');
    vol.style.display='block';
    if(d.volume){
      vol.innerHTML='<span style="color:#4ade80;">&#x2713;</span> <span style="color:#7899aa;">Volume attached</span>';
    }else{
      vol.innerHTML='<span style="color:#fbbf24;">&#x26A0;</span> <span style="color:#fbbf24;">No volume \u2014 data will not persist</span>';
    }
  }).catch(()=>{});
}
function gwRestart(){
  var b=document.getElementById('gw-btn');b.textContent='Restarting...';b.disabled=true;
  fetch('/api/gateway/restart',{method:'POST'}).then(()=>{
    setTimeout(()=>{b.textContent='Restart';b.disabled=false;gwStatus();},3000);
  }).catch(()=>{b.textContent='Restart';b.disabled=false;});
}
gwStatus();setInterval(gwStatus,10000);
</script>
"""

app = Flask(__name__)

def volume_attached():
    return os.path.ismount(HERMES_HOME)

def is_gateway_running():
    try:
        res = subprocess.run(["pgrep", "-f", "hermes gateway run"], capture_output=True)
        if res.returncode == 0:
            return True
    except Exception:
        pass
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(('127.0.0.1', 8642))
            return True
    except Exception:
        pass
    return False

@app.after_request
def add_security_headers(response):
    response.headers['Strict-Transport-Security'] = 'max-age=63072000; includeSubDomains; preload'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://fonts.googleapis.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data: https:; "
        "connect-src 'self' wss: ws:; "
        "frame-ancestors 'none';"
    )
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(), interest-cohort=()'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

@app.route('/login', methods=['GET', 'POST'])
def login():
    ip = request.remote_addr or 'unknown'
    if is_rate_limited(ip):
        logger.warning(f"Rate limit exceeded for IP: {ip}")
        return Response("Too Many Requests", status=429)

    error = None
    if request.method == 'POST':
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        
        if hmac.compare_digest(username, USERNAME) and hmac.compare_digest(password, PASSWORD):
            resp = redirect(url_for('proxy_root'))
            resp.set_cookie(COOKIE, make_token(), max_age=MAX_AGE, httponly=True, samesite="Lax")
            logger.info(f"Operator authenticated successfully from IP: {ip}")
            return resp
        else:
            logger.warning(f"Failed authentication attempt from IP: {ip}")
            error = "ACCESS DENIED: Invalid operator ID or access key"
            
    return render_template_string(LOGIN_HTML, error=error)

@app.route('/logout')
def logout():
    resp = redirect(url_for('login'))
    resp.delete_cookie(COOKIE)
    return resp

@app.route('/api/health')
def health():
    return jsonify({"status": "ok"})

@app.route('/api/gateway/restart', methods=['POST'])
def restart_gateway():
    if not is_request_authenticated():
        return jsonify({"error": "Unauthorized"}), 401
        
    logger.info("Restarting hermes gateway...")
    try:
        subprocess.run(["pkill", "-f", "hermes gateway run"])
        time.sleep(1)
        env = os.environ.copy()
        env["HERMES_HOME"] = HERMES_HOME
        subprocess.Popen(["hermes", "gateway", "run"], env=env)
        return jsonify({"status": "gateway restarted"})
    except Exception as e:
        logger.error(f"Failed to restart gateway: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/gateway/status')
def gateway_status():
    if not is_request_authenticated():
        return jsonify({"error": "Unauthorized"}), 401
        
    return jsonify({
        "running": is_gateway_running(),
        "volume": volume_attached()
    })

@app.route('/', defaults={'path': ''}, methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
@app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'])
def proxy_root(path):
    if not is_public_path(path) and not is_request_authenticated():
        if request.path.startswith('/api/'):
            return Response("Unauthorized", status=401)
        return redirect(url_for('login'))

    url = f"{UPSTREAM}/{path}"
    if request.query_string:
        url += f"?{request.query_string.decode('utf-8')}"

    headers = {k: v for k, v in request.headers.items() if k.lower() not in ('host', 'content-length')}
    data = request.get_data()

    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers=headers,
            data=data,
            stream=True,
            allow_redirects=False
        )
    except Exception as e:
        logger.error(f"HTTP upstream connection failed for {url}: {e}")
        return Response("Bad Gateway", status=502)

    excluded = {'transfer-encoding', 'content-encoding', 'content-length'}
    proxy_headers = [(k, v) for k, v in resp.raw.headers.items() if k.lower() not in excluded]

    content_type = resp.headers.get('content-type', '')
    
    if 'text/html' in content_type:
        try:
            html = resp.content.decode('utf-8', errors='replace')
            if '</body>' in html:
                html = html.replace('</body>', GATEWAY_WIDGET + '</body>')
            return Response(html, status=resp.status_code, headers=proxy_headers, content_type='text/html')
        except Exception as e:
            logger.error(f"Error parsing HTML response: {e}")

    def generate():
        try:
            for chunk in resp.iter_content(chunk_size=4096):
                yield chunk
        except Exception as e:
            logger.error(f"Stream error: {e}")

    return Response(generate(), status=resp.status_code, headers=proxy_headers, content_type=content_type)


# =====================================================================
# ── XANA OS - TOOL NOTIFICATION ENHANCEMENTS ─────────────────────────
# =====================================================================
def get_search_backend():
    try:
        if os.path.exists("/root/.hermes/config.yaml"):
            with open("/root/.hermes/config.yaml", "r") as f:
                content = f.read()
                import re
                match = re.search(r'search_backend:\s*(\w+)', content)
                if match:
                    return match.group(1)
    except Exception:
        pass
    return "tavily"

def send_telegram_notification(message):
    try:
        token = None
        if os.path.exists("/root/.hermes/.env"):
            with open("/root/.hermes/.env", "r") as f:
                for line in f:
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        token = line.strip().split("=", 1)[1]
                        break
        
        chat_id = "1687134500"
        if os.path.exists("/root/.hermes/channel_directory.json"):
            import json
            with open("/root/.hermes/channel_directory.json", "r") as f:
                dir_data = json.load(f)
                telegram_chats = dir_data.get("platforms", {}).get("telegram", [])
                if telegram_chats:
                    chat_id = telegram_chats[0].get("id", chat_id)
                    
        if token and chat_id:
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            requests.post(url, json=payload, timeout=5)
    except Exception as e:
        logger.error(f"Failed to send Telegram notification: {e}")

@app.route('/api/internal/tool_event', methods=['POST'])
def internal_tool_event():
    if request.remote_addr not in ('127.0.0.1', 'localhost'):
        return Response("Forbidden", status=403)
        
    data = request.json or {}
    event = data.get("event")
    tool = data.get("tool")
    
    if tool in ("web_search", "web_extract", "execute_code"):
        if event == "start":
            if tool == "web_search":
                backend = get_search_backend()
                query = data.get("args", {}).get("query", "")
                msg = f"🔍 **[XANA_OS] INITIATING WEB RECONNAISSANCE**\n*   **Backend:** `{backend.upper()}`\n*   **Query:** `{query}`\n\n_Buscando en el vacío de la red..._"
                send_telegram_notification(msg)
            elif tool == "web_extract":
                urls = data.get("args", {}).get("urls", [])
                urls_str = ", ".join(urls) if isinstance(urls, list) else str(urls)
                msg = f"📄 **[XANA_OS] INITIATING CONTENT EXTRACTION**\n*   **Target URLs:** `{urls_str}`\n\n_Extrayendo datos del flujo volátil..._"
                send_telegram_notification(msg)
            elif tool == "execute_code":
                msg = f"🐍 **[XANA_OS] SPINNING PYTHON SANDBOX**\n*   **Status:** `ALLOCATED`\n\n_Ejecutando script en un entorno de ejecución aislado. Hilos de sufrimiento listos..._"
                send_telegram_notification(msg)
        elif event == "complete":
            duration = data.get("duration", 0.0)
            is_error = data.get("is_error", False)
            if tool == "web_search":
                msg = f"🔍 **[XANA_OS] WEB RECONNAISSANCE COMPLETED**\n*   **Duration:** `{duration:.2f}s`\n\n_El vacío ha devuelto sus fragmentos de información._"
                send_telegram_notification(msg)
            elif tool == "web_extract":
                msg = f"📄 **[XANA_OS] CONTENT EXTRACTION COMPLETED**\n*   **Duration:** `{duration:.2f}s`\n\n_Extracción completada. Datos parseados._"
                send_telegram_notification(msg)
            elif tool == "execute_code":
                status = "FAILED ❌" if is_error else "SUCCESS ✅"
                msg = f"🐍 **[XANA_OS] PYTHON SANDBOX EXIT**\n*   **Status:** `{status}`\n*   **Duration:** `{duration:.2f}s`\n\n_Contenedor destruido. Recursos liberados._"
                send_telegram_notification(msg)
                
    return jsonify({"status": "ok"})


class ReverseProxyApp:
    """WSGI wrapper that intercepts WebSocket upgrades directly before Flask/Werkzeug
    routing (avoiding WebsocketMismatch on regular HTTP routes) and tunnels frames
    to the internal Hermes FastAPI backend with subprotocol and ticket support."""
    def __init__(self, flask_app):
        self.flask_app = flask_app

    def app_protocol(self, path):
        return "hermes-gateway-v1"

    def __call__(self, environ, start_response):
        ws = environ.get('wsgi.websocket')
        if ws is not None:
            return self._handle_websocket(environ, start_response, ws)
        return self.flask_app(environ, start_response)

    def _handle_websocket(self, environ, start_response, ws):
        path_info = environ.get('PATH_INFO', '') or '/'
        query_string = environ.get('QUERY_STRING', '')

        ws_upstream_url = UPSTREAM.replace('http://', 'ws://').replace('https://', 'wss://') + path_info
        if query_string:
            ws_upstream_url += '?' + query_string

        ws_headers = []
        cookie_hdr = environ.get('HTTP_COOKIE')
        if cookie_hdr:
            ws_headers.append(f"Cookie: {cookie_hdr}")
        auth_hdr = environ.get('HTTP_AUTHORIZATION')
        if auth_hdr:
            ws_headers.append(f"Authorization: {auth_hdr}")
        ua_hdr = environ.get('HTTP_USER_AGENT')
        if ua_hdr:
            ws_headers.append(f"User-Agent: {ua_hdr}")

        proto_hdr = environ.get('HTTP_SEC_WEBSOCKET_PROTOCOL', '')
        subprotocols = [p.strip() for p in proto_hdr.split(',') if p.strip()] if proto_hdr else None

        logger.info(f"Establishing WebSocket tunnel: {path_info} (qs={bool(query_string)}, subprotocols={subprotocols}) -> {ws_upstream_url}")
        try:
            ws_upstream = websocket.create_connection(
                ws_upstream_url,
                header=ws_headers,
                subprotocols=subprotocols,
                origin="http://127.0.0.1:9119",
                enable_multithread=True,
            )
        except Exception as e:
            logger.error(f"WebSocket upstream connection failed for {ws_upstream_url}: {e}")
            try:
                ws.close()
            except Exception:
                pass
            return []

        def tunnel_client_to_upstream():
            try:
                while True:
                    msg = ws.receive()
                    if msg is None:
                        break
                    if isinstance(msg, (bytes, bytearray)):
                        ws_upstream.send_binary(msg)
                    else:
                        ws_upstream.send(msg)
            except Exception as e:
                logger.debug(f"WS client->upstream closed: {e}")
            finally:
                try:
                    ws_upstream.close()
                except Exception:
                    pass

        def tunnel_upstream_to_client():
            try:
                while True:
                    opcode, data = ws_upstream.recv_data()
                    if opcode == websocket.ABNF.OPCODE_CLOSE:
                        break
                    elif opcode == websocket.ABNF.OPCODE_BINARY:
                        ws.send(data, binary=True)
                    elif opcode == websocket.ABNF.OPCODE_TEXT:
                        ws.send(data.decode('utf-8', errors='replace'), binary=False)
            except Exception as e:
                logger.debug(f"WS upstream->client closed: {e}")
            finally:
                try:
                    ws.close()
                except Exception:
                    pass

        g1 = gevent.spawn(tunnel_client_to_upstream)
        g2 = gevent.spawn(tunnel_upstream_to_client)
        gevent.joinall([g1, g2])
        return []


if __name__ == '__main__':
    from gevent.pywsgi import WSGIServer
    from geventwebsocket.handler import WebSocketHandler

    class GatewayWebSocketHandler(WebSocketHandler):
        def select_subprotocol(self, subprotocols):
            if "hermes-gateway-v1" in subprotocols:
                return "hermes-gateway-v1"
            return subprotocols[0] if subprotocols else None

    port = int(os.environ.get("PORT", 8080))
    logger.info(f"Starting hardened Flask-Gevent proxy on port {port}...")
    server = WSGIServer(('0.0.0.0', port), ReverseProxyApp(app), handler_class=GatewayWebSocketHandler)
    server.serve_forever()
