from flask import Flask, Response, jsonify, redirect, render_template_string, request, send_file, url_for
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import threading
from pathlib import Path
from PIL import Image
from urllib.parse import parse_qsl, urlencode, urlparse
import os
import requests

PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "192.168.1.252")
FLASK_PORT = os.environ.get("FLASK_PORT", "5050")
PIXLET_PORT = os.environ.get("PIXLET_PORT", "8080")
PIXLET_INTERNAL_PORT = os.environ.get("PIXLET_INTERNAL_PORT", "18080")
BROWSER_PIXLET_URL = os.environ.get(
    "BROWSER_PIXLET_URL",
    f"http://{PUBLIC_HOST}:{PIXLET_PORT}/"
)
ESP32_FRAME_URL = os.environ.get(
    "ESP32_FRAME_URL",
    f"http://{PUBLIC_HOST}:{FLASK_PORT}/frame.rgb565"
)
MATRIX_WIDTH = int(os.environ.get("MATRIX_WIDTH", "64"))
MATRIX_HEIGHT = int(os.environ.get("MATRIX_HEIGHT", "32"))
FRAME_BYTE_COUNT = MATRIX_WIDTH * MATRIX_HEIGHT * 2

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
APPS_DIR = BASE_DIR / "apps"
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
RENDER_DIR = Path(os.environ.get("RENDER_DIR", BASE_DIR / "renders"))
OPTIONS_FILE = DATA_DIR / "app_options.json"
STATE_FILE = DATA_DIR / "state.json"
LEGACY_OPTIONS_FILE = BASE_DIR / "app_options.json"
DATA_DIR.mkdir(exist_ok=True)
RENDER_DIR.mkdir(exist_ok=True)

pixlet_process = None
preview_proxy_server = None
current_app = None
current_app_path = None

PAGE = """
<!doctype html>
<html>
<head>
    <title>Matrix App Picker</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #111; color: #f5f5f5; }
        h1 { margin-bottom: 10px; }
        .status { margin-bottom: 25px; padding: 12px; background: #222; border-radius: 8px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
        .card { background: #222; padding: 14px; border-radius: 8px; }
        button { width: 100%; padding: 10px; font-size: 15px; cursor: pointer; border: 0; border-radius: 6px; margin-top: 8px; }
        input { width: 100%; box-sizing: border-box; padding: 8px; border-radius: 6px; border: 1px solid #444; background: #111; color: #f5f5f5; margin-top: 8px; }
        label { display: block; font-size: 13px; color: #bbb; margin-top: 10px; }
        .hint { font-size: 12px; color: #aaa; margin-top: 6px; line-height: 1.35; }
        a { color: #8ab4ff; }
        img { margin-top: 14px; image-rendering: pixelated; width: 256px; height: 128px; border: 1px solid #444; }
    </style>
</head>
<body>
    <h1>Matrix App Picker</h1>

    <div class="status">
        <strong>Current app:</strong> {{ current_app or "None selected" }}<br>
        <strong>Pixlet preview:</strong> <a href="{{ browser_pixlet_url }}" target="_blank">Open Pixlet preview</a><br>
        <strong>ESP32 frame endpoint:</strong> <a href="{{ esp32_frame_url }}" target="_blank">{{ esp32_frame_url }}</a><br>
        <strong>ESP32 config:</strong> <a href="/esp32-config" target="_blank">/esp32-config</a><br>
        {% if current_app %}
            <img src="/frame.webp?cache={{ cache_bust }}" alt="Current rendered frame">
        {% endif %}
        {% if current_app %}
        <form method="post" action="/save-options" style="margin-top: 14px;">
            <input type="hidden" name="app_path" value="{{ current_app }}">
            <label style="font-size: 13px; color: #bbb; display: block; margin-bottom: 4px;">
                Quick save settings for <strong>{{ current_app }}</strong> &mdash; paste the full URL (or just the <code>?…</code> part) from the Pixlet preview tab
            </label>
            <div style="display: flex; gap: 8px;">
                <input name="options" value="{{ options_map.get(current_app, '') }}" placeholder="{{ browser_pixlet_url }}?param=value&amp;…" style="flex: 1; margin-top: 0;">
                <button type="submit" style="width: auto; padding: 8px 16px; background: #2a7a4a; color: white; margin-top: 0;">Save Settings</button>
            </div>
        </form>
        {% else %}
        <div class="hint">
            Run an app, then paste its Pixlet preview URL here to save settings.
        </div>
        {% endif %}
    </div>

    <div class="grid">
        {% for app_file in app_files %}
        <div class="card">
            <strong>{{ app_file }}</strong>

            <form method="post" action="/run" onsubmit="window.open('/preview-loader?app_path=' + encodeURIComponent(this.app_path.value), '_blank');">
                <input type="hidden" name="app_path" value="{{ app_file }}">
                <button type="submit">Run App</button>
            </form>

            <form method="post" action="/save-options">
                <input type="hidden" name="app_path" value="{{ app_file }}">
                <label>Saved options</label>
                <input name="options" value="{{ options_map.get(app_file, '') }}" placeholder="Example: city=Fort%20Worth&units=imperial">
                <button type="submit">Save Options</button>
            </form>

            {% if options_map.get(app_file) %}
                <div class="hint">Saved: {{ options_map.get(app_file) }}</div>
            {% else %}
                <div class="hint">No saved options yet.</div>
            {% endif %}
        </div>
        {% endfor %}
    </div>
</body>
</html>
"""

RUNNING_PAGE = """
<!doctype html>
<html>
<head>
    <title>Starting Pixlet App</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #111; color: #f5f5f5; }
        a { color: #8ab4ff; }
    </style>
</head>
<body>
    <h1>Starting {{ app_name }}</h1>
    <p>A new Pixlet preview tab should open automatically.</p>
    <p><a href="{{ browser_pixlet_url }}" target="_blank">Open Pixlet preview manually</a></p>

    <script>
        setTimeout(function() {
            window.location.href = '/';
        }, 800);
    </script>
</body>
</html>
"""

PREVIEW_LOADER_PAGE = """
<!doctype html>
<html>
<head>
    <title>Loading Pixlet Preview</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #111; color: #f5f5f5; }
        a { color: #8ab4ff; }
    </style>
</head>
<body>
    <h1>Loading Pixlet Preview...</h1>
    <p>This tab will switch to the Pixlet preview automatically.</p>
    <p><a href="{{ preview_url }}" id="manual-link">Open preview manually</a></p>

    <script>
        setTimeout(function() {
            const previewUrl = {{ preview_url|tojson }};
            const separator = previewUrl.includes('?') ? '&' : '?';
            window.location.href = previewUrl + separator + 'cache=' + Date.now();
        }, 1200);
    </script>
</body>
</html>
"""


def find_apps():
    return sorted(str(path.relative_to(APPS_DIR)) for path in APPS_DIR.rglob("*.star"))


def load_options():
    options_file = OPTIONS_FILE if OPTIONS_FILE.exists() else LEGACY_OPTIONS_FILE

    if not options_file.exists():
        return {}

    try:
        return json.loads(options_file.read_text())
    except json.JSONDecodeError:
        return {}


def save_options(options):
    OPTIONS_FILE.write_text(json.dumps(options, indent=2, sort_keys=True))


def save_state():
    if current_app:
        STATE_FILE.write_text(json.dumps({"current_app": current_app}, indent=2))
    elif STATE_FILE.exists():
        STATE_FILE.unlink()


def normalize_options(raw_options):
    raw_options = (raw_options or "").strip()

    if not raw_options:
        return ""

    parsed_url = urlparse(raw_options)
    if parsed_url.query:
        raw_options = parsed_url.query

    if raw_options.startswith("?"):
        raw_options = raw_options[1:]

    pairs = [
        (key, value)
        for key, value in parse_qsl(raw_options, keep_blank_values=True)
        if key != "cache"
    ]
    return urlencode(pairs)


def options_to_pixlet_args(query_string):
    return [f"{key}={value}" for key, value in parse_qsl(query_string or "", keep_blank_values=True)]


def preview_url_for(host=None, app_name=None):
    options = load_options()
    query_string = options.get(app_name or current_app, "")
    return f"{BROWSER_PIXLET_URL}?{query_string}" if query_string else BROWSER_PIXLET_URL


def stop_pixlet():
    global pixlet_process

    if pixlet_process and pixlet_process.poll() is None:
        pixlet_process.terminate()
        try:
            pixlet_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pixlet_process.kill()

    pixlet_process = None


def start_pixlet(full_path):
    return subprocess.Popen(
        ["pixlet", "serve", "--host", "127.0.0.1", "--port", PIXLET_INTERNAL_PORT, str(full_path)],
        cwd=BASE_DIR,
    )


def save_options_for_app(app_path, raw_options):
    full_path = (APPS_DIR / app_path).resolve()

    if not str(full_path).startswith(str(APPS_DIR.resolve())):
        return False, "Invalid app path"

    if not full_path.exists() or full_path.suffix != ".star":
        return False, "App not found"

    options = load_options()
    normalized = normalize_options(raw_options)

    if normalized:
        options[app_path] = normalized
    else:
        options.pop(app_path, None)

    save_options(options)
    return True, normalized


def no_app_preview_html():
    return f"""<!doctype html>
<html>
<head>
    <title>Pixlet Preview</title>
    <style>
        body {{ margin: 0; padding: 40px; background: #111; color: #f5f5f5; font-family: Arial, sans-serif; }}
        a {{ color: #8ab4ff; }}
    </style>
</head>
<body>
    <h1>No Pixlet app is running</h1>
    <p>Open the dashboard and click Run App. The preview will show here and settings will autosave.</p>
    <p><a href="http://{PUBLIC_HOST}:{FLASK_PORT}/">Open dashboard</a></p>
</body>
</html>"""


def autosave_script():
    if current_app:
        return f"""
        <script>
        (function() {{
            const statusEl = document.createElement('div');
            statusEl.style.cssText = 'position:fixed;right:10px;bottom:10px;z-index:2147483647;background:#181818;color:#f5f5f5;border:1px solid #333;border-radius:6px;padding:7px 10px;font:12px Arial,sans-serif;box-shadow:0 2px 10px rgba(0,0,0,.35);';
            statusEl.textContent = 'Settings autosave is ready.';
            document.addEventListener('DOMContentLoaded', function() {{
                document.body.appendChild(statusEl);
            }});

            let lastSavedSearch = "";
            let saveTimer = null;

            function setStatus(message) {{
                statusEl.textContent = message;
            }}

            async function saveSearch(search) {{
                if (!search || search === "?" || search === lastSavedSearch) {{
                    return;
                }}

                lastSavedSearch = search;
                setStatus("Saving settings...");

                const response = await fetch('/__save-options', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/json'}},
                    body: JSON.stringify({{query: search}})
                }});

                if (!response.ok) {{
                    setStatus("Autosave failed.");
                    return;
                }}

                setStatus("Settings saved.");
            }}

            function checkForSettingsChange() {{
                const search = window.location.search;
                clearTimeout(saveTimer);
                saveTimer = setTimeout(function() {{
                    saveSearch(search).catch(function() {{
                        setStatus("Autosave failed.");
                    }});
                }}, 500);
            }}

            window.addEventListener('popstate', checkForSettingsChange);
            window.addEventListener('hashchange', checkForSettingsChange);
            const originalPushState = history.pushState;
            const originalReplaceState = history.replaceState;
            history.pushState = function() {{
                const result = originalPushState.apply(this, arguments);
                checkForSettingsChange();
                return result;
            }};
            history.replaceState = function() {{
                const result = originalReplaceState.apply(this, arguments);
                checkForSettingsChange();
                return result;
            }};

            checkForSettingsChange();
            setInterval(checkForSettingsChange, 1000);
        }})();
        </script>
        """

    return ""


class PixletPreviewProxyHandler(BaseHTTPRequestHandler):
    server_version = "MatrixPixletPreview/1.0"

    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/" and not current_app:
            self.send_no_app_preview()
            return
        if parsed.path == "/" and current_app and not parsed.query:
            saved_query = load_options().get(current_app, "")
            if saved_query:
                self.send_response(302)
                self.send_header("Location", f"/?{saved_query}")
                self.end_headers()
                return

        self.proxy_to_pixlet(inject_autosave=parsed.path == "/")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/__save-options":
            self.save_current_options()
            return

        self.proxy_to_pixlet()

    def do_PUT(self):
        self.proxy_to_pixlet()

    def do_DELETE(self):
        self.proxy_to_pixlet()

    def send_no_app_preview(self):
        content = no_app_preview_html().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def save_current_options(self):
        if not current_app:
            self.send_json({"ok": False, "error": "No app is running"}, status=409)
            return

        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b"{}"

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_json({"ok": False, "error": "Invalid JSON"}, status=400)
            return

        ok, result = save_options_for_app(current_app, payload.get("query", ""))
        if not ok:
            self.send_json({"ok": False, "error": result}, status=400)
            return

        self.send_json({"ok": True, "app": current_app, "options": result})

    def send_json(self, payload, status=200):
        content = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def proxy_to_pixlet(self, inject_autosave=False):
        parsed = urlparse(self.path)
        target_path = parsed.path

        target_url = f"http://127.0.0.1:{PIXLET_INTERNAL_PORT}{target_path}"
        if parsed.query:
            target_url = f"{target_url}?{parsed.query}"

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "content-length", "connection"}
        }

        try:
            response = requests.request(
                self.command,
                target_url,
                headers=headers,
                data=body,
                allow_redirects=False,
                timeout=30,
            )
        except requests.RequestException as error:
            content = f"Pixlet preview is not ready: {error}".encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        content = response.content
        content_type = response.headers.get("Content-Type", "")
        if inject_autosave and "text/html" in content_type:
            script = autosave_script().encode("utf-8")
            if b"</body>" in content:
                content = content.replace(b"</body>", script + b"</body>", 1)
            else:
                content += script

        self.send_response(response.status_code)
        for key, value in response.headers.items():
            if key.lower() in {"content-length", "transfer-encoding", "connection", "content-encoding"}:
                continue
            self.send_header(key, value)

        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def start_preview_proxy():
    global preview_proxy_server

    preview_proxy_server = ThreadingHTTPServer(("0.0.0.0", int(PIXLET_PORT)), PixletPreviewProxyHandler)
    thread = threading.Thread(target=preview_proxy_server.serve_forever, daemon=True)
    thread.start()


def restore_last_app():
    global pixlet_process, current_app, current_app_path

    if not STATE_FILE.exists():
        return

    try:
        state = json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return

    app_path = state.get("current_app")
    if not app_path:
        return

    full_path = (APPS_DIR / app_path).resolve()
    if not str(full_path).startswith(str(APPS_DIR.resolve())):
        return

    if not full_path.exists() or full_path.suffix != ".star":
        return

    try:
        pixlet_process = start_pixlet(full_path)
    except OSError as error:
        print(f"Failed to restore Pixlet preview for {app_path}: {error}")
        return

    current_app = app_path
    current_app_path = full_path


def render_current_webp():
    if not current_app_path:
        return None, "No app selected"

    output_file = RENDER_DIR / "current.webp"
    if output_file.exists():
        output_file.unlink()

    options = load_options()
    pixlet_args = options_to_pixlet_args(options.get(current_app, ""))

    result = subprocess.run(
        ["pixlet", "render", str(current_app_path), *pixlet_args, "-o", str(output_file)],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None, result.stderr

    if not output_file.exists():
        return None, "Pixlet rendered, but current.webp was not created"

    return output_file, None


def webp_to_rgb565(webp_file):
    image = Image.open(webp_file).convert("RGB").resize((MATRIX_WIDTH, MATRIX_HEIGHT))
    data = bytearray()

    for y in range(MATRIX_HEIGHT):
        for x in range(MATRIX_WIDTH):
            r, g, b = image.getpixel((x, y))
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            data.append((rgb565 >> 8) & 0xFF)
            data.append(rgb565 & 0xFF)

    return bytes(data)


@app.route("/")
def home():
    host = request.host.split(":")[0]
    options_map = load_options()
    return render_template_string(
        PAGE,
        app_files=find_apps(),
        current_app=current_app,
        host=host,
        current_preview_url=preview_url_for(host),
        browser_pixlet_url=BROWSER_PIXLET_URL,
        esp32_frame_url=ESP32_FRAME_URL,
        options_map=options_map,
        cache_bust=f"{current_app}-{options_map.get(current_app, '')}",
    )


@app.route("/preview-loader")
def preview_loader():
    app_path = request.args.get("app_path")
    preview_url = preview_url_for(app_name=app_path)
    return render_template_string(PREVIEW_LOADER_PAGE, preview_url=preview_url)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "current_app": current_app, "saved_options": load_options().get(current_app, "")})


@app.route("/esp32-config")
def esp32_config():
    return jsonify({
        "frame_url": ESP32_FRAME_URL,
        "width": MATRIX_WIDTH,
        "height": MATRIX_HEIGHT,
        "pixel_format": "rgb565",
        "byte_order": "big_endian",
        "frame_bytes": FRAME_BYTE_COUNT,
        "refresh_ms": 1000,
        "current_app": current_app,
    })


@app.route("/run", methods=["POST"])
def run_app():
    global pixlet_process, current_app, current_app_path

    app_path = request.form.get("app_path")
    if not app_path:
        return "Missing app_path", 400

    full_path = (APPS_DIR / app_path).resolve()

    if not str(full_path).startswith(str(APPS_DIR.resolve())):
        return "Invalid app path", 400

    if not full_path.exists() or full_path.suffix != ".star":
        return "App not found", 404

    stop_pixlet()

    try:
        pixlet_process = start_pixlet(full_path)
    except OSError as error:
        return f"Failed to start Pixlet: {error}", 500

    current_app = app_path
    current_app_path = full_path
    save_state()

    host = request.host.split(":")[0]
    return render_template_string(
        RUNNING_PAGE,
        app_name=app_path,
        host=host,
        preview_url=preview_url_for(host, app_path),
        browser_pixlet_url=BROWSER_PIXLET_URL,
    )


@app.route("/save-options", methods=["POST"])
def save_app_options():
    app_path = request.form.get("app_path")
    raw_options = request.form.get("options", "")

    if not app_path:
        return "Missing app_path", 400

    full_path = (APPS_DIR / app_path).resolve()

    if not str(full_path).startswith(str(APPS_DIR.resolve())):
        return "Invalid app path", 400

    if not full_path.exists() or full_path.suffix != ".star":
        return "App not found", 404

    ok, error = save_options_for_app(app_path, raw_options)
    if not ok:
        return error, 400

    return redirect(url_for("home"))


@app.route("/frame.webp")
def frame_webp():
    webp_file, error = render_current_webp()
    if error:
        return error, 500

    return send_file(webp_file, mimetype="image/webp")


@app.route("/frame.rgb565")
def frame_rgb565():
    webp_file, error = render_current_webp()
    if error and not current_app_path:
        rgb565 = bytes(FRAME_BYTE_COUNT)
        return Response(
            rgb565,
            mimetype="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Matrix-Width": str(MATRIX_WIDTH),
                "X-Matrix-Height": str(MATRIX_HEIGHT),
                "X-Pixel-Format": "rgb565",
            },
        )
    if error:
        return error, 500

    rgb565 = webp_to_rgb565(webp_file)
    return Response(
        rgb565,
        mimetype="application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Matrix-Width": str(MATRIX_WIDTH),
            "X-Matrix-Height": str(MATRIX_HEIGHT),
            "X-Pixel-Format": "rgb565",
        },
    )


@app.route("/stop", methods=["POST"])
def stop_app():
    global current_app, current_app_path
    stop_pixlet()
    current_app = None
    current_app_path = None
    save_state()
    return redirect(url_for("home"))


if __name__ == "__main__":
    start_preview_proxy()
    restore_last_app()
    app.run(host="0.0.0.0", port=int(FLASK_PORT))
