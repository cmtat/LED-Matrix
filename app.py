from flask import Flask, Response, jsonify, redirect, render_template_string, request, send_file, stream_with_context, url_for
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import threading
import time
from pathlib import Path
from PIL import Image
from urllib.parse import parse_qsl, urlencode, urlparse
import os
import requests
import socket

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
TARGET_STREAM_FPS = max(int(os.environ.get("TARGET_STREAM_FPS", "30")), 1)
TARGET_STREAM_FRAME_MS = 1000 / TARGET_STREAM_FPS
TARGET_STREAM_FRAME_SECONDS = TARGET_STREAM_FRAME_MS / 1000
STREAM_SPIKE_LOG_MS = float(os.environ.get("STREAM_SPIKE_LOG_MS", "50"))
STREAM_STATS_LOG_SECONDS = float(os.environ.get("STREAM_STATS_LOG_SECONDS", "5"))
STREAM_SOCKET_SNDBUF = int(os.environ.get("STREAM_SOCKET_SNDBUF", "262144"))

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
frame_cache_lock = threading.RLock()
frame_cache = {
    "frames": [bytes(FRAME_BYTE_COUNT)],
    "rgb_frames": [],
    "durations": [1000],
    "total_duration": 1000,
    "started_at": time.monotonic(),
    "rendered_at": None,
    "key": None,
    "error_key": None,
    "error": None,
    "webp_file": None,
    "metadata": [],
}
background_render_key = None
latest_frame_lock = threading.Condition()
latest_frame = {
    "frame": bytes(FRAME_BYTE_COUNT),
    "sequence": 0,
    "prepared_at": time.monotonic(),
    "render_ms": 0.0,
    "interval_ms": 0.0,
    "error": None,
}
frame_producer_thread = None
runtime_services_lock = threading.Lock()
runtime_services_started = False
CONSTANT_TEST_FRAME = bytes(FRAME_BYTE_COUNT)

LIVE_RENDER_APP_REFRESH_SECONDS = {
    "Death Clock/death_clock.star": 60,
}

PAGE = """
<!doctype html>
<html>
<head>
    <title>Matrix App Picker</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {
            color-scheme: dark;
            --bg: #0f1115;
            --panel: #181b22;
            --panel-soft: #20242d;
            --line: #303642;
            --text: #f4f6fb;
            --muted: #a6adbb;
            --accent: #35c28f;
            --accent-strong: #21a976;
            --link: #8db7ff;
            --danger: #d75d5d;
        }

        * { box-sizing: border-box; }

        body {
            margin: 0;
            background: var(--bg);
            color: var(--text);
            font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            min-height: 100vh;
        }

        .shell {
            width: min(1180px, calc(100% - 32px));
            margin: 0 auto;
            padding: 28px 0 40px;
        }

        .topbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 20px;
        }

        h1 {
            margin: 0;
            font-size: 28px;
            font-weight: 760;
            line-height: 1.1;
        }

        .subtle {
            margin: 6px 0 0;
            color: var(--muted);
            font-size: 14px;
        }

        .status-dot {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            min-height: 34px;
            padding: 0 12px;
            border: 1px solid var(--line);
            border-radius: 999px;
            background: var(--panel);
            color: var(--muted);
            font-size: 13px;
            white-space: nowrap;
        }

        .status-dot::before {
            content: "";
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: {% if current_app %}var(--accent){% else %}#687080{% endif %};
        }

        .hero {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 18px;
            padding: 18px;
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--panel);
            margin-bottom: 22px;
        }

        .current {
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 18px;
            min-width: 0;
        }

        .eyebrow {
            color: var(--muted);
            font-size: 12px;
            font-weight: 700;
            letter-spacing: .08em;
            text-transform: uppercase;
        }

        .current-title {
            margin: 8px 0 0;
            font-size: 22px;
            font-weight: 720;
            overflow-wrap: anywhere;
        }

        .links {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }

        .links form {
            margin: 0;
        }

        a, button {
            font: inherit;
        }

        a {
            color: var(--link);
            text-decoration: none;
        }

        a:hover {
            text-decoration: underline;
        }

        .button, button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 38px;
            padding: 0 14px;
            border: 1px solid transparent;
            border-radius: 6px;
            background: var(--accent);
            color: #04150f;
            font-weight: 700;
            cursor: pointer;
            text-decoration: none;
            white-space: nowrap;
        }

        .button:hover {
            text-decoration: none;
            background: var(--accent-strong);
        }

        .button.secondary {
            background: var(--panel-soft);
            color: var(--text);
            border-color: var(--line);
        }

        .button.secondary:hover {
            background: #262b35;
        }

        .button.danger {
            background: transparent;
            color: #ffaaaa;
            border-color: #6e3434;
        }

        .button.danger:hover {
            background: #2b1719;
        }

        .section-head {
            display: flex;
            align-items: end;
            justify-content: space-between;
            gap: 16px;
            margin: 24px 0 12px;
        }

        h2 {
            margin: 0;
            font-size: 18px;
        }

        .count {
            color: var(--muted);
            font-size: 13px;
        }

        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
            gap: 12px;
        }

        .card {
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 16px;
            min-height: 132px;
            padding: 14px;
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--panel);
        }

        .card.active {
            border-color: rgba(53, 194, 143, .8);
        }

        .app-name {
            font-weight: 720;
            line-height: 1.25;
            overflow-wrap: anywhere;
        }

        .app-meta {
            margin-top: 7px;
            color: var(--muted);
            font-size: 13px;
        }

        .card-actions {
            display: flex;
            gap: 8px;
        }

        .card-actions form {
            flex: 1;
        }

        .card-actions button,
        .card-actions .button {
            width: 100%;
        }

        @media (max-width: 720px) {
            .shell {
                width: min(100% - 20px, 1180px);
                padding-top: 18px;
            }

            .topbar, .section-head {
                align-items: flex-start;
                flex-direction: column;
            }

            .hero {
                align-items: stretch;
                flex-direction: column;
            }

            .links,
            .links form {
                width: 100%;
            }

            .links .button,
            .links button {
                width: 100%;
            }

            .grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>
<body>
    <main class="shell">
        <header class="topbar">
            <div>
                <h1>Matrix App Picker</h1>
                <p class="subtle">Choose what your LED matrix shows next.</p>
            </div>
            <div class="status-dot">{{ "Running" if current_app else "Idle" }}</div>
        </header>

        <section class="hero">
            <div class="current">
                <div>
                    <div class="eyebrow">Current app</div>
                    <div class="current-title">{{ current_app_display_name or "Nothing selected" }}</div>
                </div>

                <div class="links">
                    <a class="button" href="{{ browser_pixlet_url }}" target="_blank">Preview</a>
                    <a class="button secondary" href="{{ esp32_frame_url }}" target="_blank">Frame Endpoint</a>
                    <a class="button secondary" href="/esp32-config" target="_blank">ESP32 Config</a>
                    {% if current_app %}
                    <form method="post" action="/stop">
                        <button class="button danger" type="submit">Stop</button>
                    </form>
                    {% endif %}
                </div>
            </div>
        </section>

        <div class="section-head">
            <h2>Apps</h2>
            <div class="count">{{ app_files|length }} available</div>
        </div>

        <section class="grid">
            {% for app_file in app_files %}
            <article class="card {% if app_file == current_app %}active{% endif %}">
                <div>
                    <div class="app-name">{{ app_display_names.get(app_file, app_file) }}</div>
                    <div class="app-meta">
                        {% if app_file == current_app %}
                            Running now
                        {% elif options_map.get(app_file) %}
                            Saved settings
                        {% else %}
                            Default settings
                        {% endif %}
                    </div>
                </div>

                <div class="card-actions">
                    <form method="post" action="/run">
                        <input type="hidden" name="app_path" value="{{ app_file }}">
                        <button type="submit">{{ "Restart" if app_file == current_app else "Run" }}</button>
                    </form>
                    {% if app_file == current_app %}
                    <a class="button secondary" href="{{ browser_pixlet_url }}" target="_blank">Preview</a>
                    {% endif %}
                </div>
            </article>
            {% endfor %}
        </section>
    </main>
</body>
</html>
"""

def find_apps():
    return sorted(str(path.relative_to(APPS_DIR)) for path in APPS_DIR.rglob("*.star"))


def fallback_app_display_name(app_path):
    stem = Path(app_path).stem
    return stem.replace("_", " ").replace("-", " ").title()


def manifest_name_for_app(app_path):
    manifest_path = (APPS_DIR / app_path).parent / "manifest.yaml"
    if not manifest_path.exists():
        return fallback_app_display_name(app_path)

    try:
        for line in manifest_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("name:"):
                return stripped.split(":", 1)[1].strip().strip("\"'") or fallback_app_display_name(app_path)
    except OSError:
        pass

    return fallback_app_display_name(app_path)


def app_display_names(app_files):
    return {app_path: manifest_name_for_app(app_path) for app_path in app_files}


def app_display_name(app_path):
    if not app_path:
        return None

    return manifest_name_for_app(app_path)


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


def current_frame_cache_key():
    if not current_app or not current_app_path:
        return None

    options = load_options().get(current_app, "")
    try:
        app_mtime = current_app_path.stat().st_mtime
    except OSError:
        app_mtime = None

    refresh_seconds = LIVE_RENDER_APP_REFRESH_SECONDS.get(current_app)
    refresh_bucket = int(time.time() // refresh_seconds) if refresh_seconds else None

    return (current_app, str(current_app_path), app_mtime, options, refresh_bucket)


def reset_frame_cache():
    with frame_cache_lock:
        frame_cache["frames"] = [bytes(FRAME_BYTE_COUNT)]
        frame_cache["rgb_frames"] = []
        frame_cache["durations"] = [1000]
        frame_cache["total_duration"] = 1000
        frame_cache["started_at"] = time.monotonic()
        frame_cache["rendered_at"] = None
        frame_cache["key"] = None
        frame_cache["error_key"] = None
        frame_cache["error"] = None
        frame_cache["webp_file"] = None
        frame_cache["metadata"] = []
    publish_latest_frame(bytes(FRAME_BYTE_COUNT), render_ms=0, interval_ms=0)


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
    reset_frame_cache()
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

            let lastSavedSearch = null;
            let saveTimer = null;

            function setStatus(message) {{
                statusEl.textContent = message;
            }}

            async function saveSearch(search) {{
                if (search === "?" || search === lastSavedSearch) {{
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


def render_current_webp(output_file=None):
    if not current_app_path:
        return None, None, None, "No app selected"

    if output_file is None:
        output_file = RENDER_DIR / f"current-{int(time.time() * 1000)}-{threading.get_ident()}.webp"

    options = load_options()
    pixlet_args = options_to_pixlet_args(options.get(current_app, ""))

    render_started_monotonic = time.monotonic()
    render_started_wall = time.time()
    result = subprocess.run(
        ["pixlet", "render", str(current_app_path), *pixlet_args, "-o", str(output_file)],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        return None, None, None, result.stderr

    if not output_file.exists():
        return None, None, None, "Pixlet rendered, but current.webp was not created"

    return output_file, render_started_monotonic, render_started_wall, None


def cache_key_without_refresh_bucket(key):
    return key[:-1] if key else None


def is_live_render_key(key):
    app_name = key[0] if key else None
    return bool(LIVE_RENDER_APP_REFRESH_SECONDS.get(app_name))


def should_refresh_live_render(key, started_at, total_duration):
    if not is_live_render_key(key) or total_duration <= 0:
        return False

    app_name = key[0]
    refresh_seconds = LIVE_RENDER_APP_REFRESH_SECONDS[app_name]
    refresh_after_ms = min(refresh_seconds * 500, max(total_duration // 2, 1))
    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    return elapsed_ms >= refresh_after_ms


def tile_extents(frame):
    extents = []
    for tile in getattr(frame, "tile", []) or []:
        box = getattr(tile, "extents", None)
        if box is None and len(tile) > 1:
            box = tile[1]
        if box:
            extents.append(tuple(int(value) for value in box))
    return extents


def normalized_update_box(frame, canvas_size):
    boxes = tile_extents(frame)
    if not boxes:
        return (0, 0) + canvas_size

    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    bottom = max(box[3] for box in boxes)
    width, height = canvas_size

    update_box = (
        max(0, min(left, width)),
        max(0, min(top, height)),
        max(0, min(right, width)),
        max(0, min(bottom, height)),
    )

    if update_box[2] <= update_box[0] or update_box[3] <= update_box[1]:
        return (0, 0) + canvas_size

    return update_box


def frame_metadata(image, frame, index, update_box):
    disposal = getattr(frame, "disposal_method", None)
    if disposal is None:
        disposal = frame.info.get("disposal")
    blend = getattr(frame, "blend", frame.info.get("blend"))

    return {
        "index": index,
        "source_size": list(image.size),
        "frame_size": list(frame.size),
        "mode": frame.mode,
        "duration_ms": int(frame.info.get("duration", 33) or 33),
        "timestamp": frame.info.get("timestamp"),
        "tile_extents": [list(box) for box in tile_extents(frame)],
        "update_box": list(update_box),
        "disposal": None if disposal is None else str(disposal),
        "blend": None if blend is None else str(blend),
    }


def composited_animation_frames(image):
    canvas_size = image.size
    background = Image.new("RGBA", canvas_size, image.info.get("background", (0, 0, 0, 0)))
    canvas = background.copy()
    rgb_frames = []
    durations = []
    metadata = []
    frame_count = getattr(image, "n_frames", 1)

    for index in range(frame_count):
        image.seek(index)
        update_box = normalized_update_box(image, canvas_size)
        frame_rgba = image.copy().convert("RGBA")

        if frame_rgba.size == canvas_size:
            if update_box == (0, 0) + canvas_size:
                update = frame_rgba
                paste_box = (0, 0)
            else:
                update = frame_rgba.crop(update_box)
                paste_box = update_box[:2]
        else:
            update = frame_rgba
            paste_box = update_box[:2]

        canvas.alpha_composite(update, dest=paste_box)
        rgb_frames.append(canvas.convert("RGB").resize((MATRIX_WIDTH, MATRIX_HEIGHT)))

        duration = int(image.info.get("duration", frame_rgba.info.get("duration", 33)) or 33)
        durations.append(max(duration, 1))
        metadata.append(frame_metadata(image, image, index, update_box))

    if not rgb_frames:
        rgb_frames = [Image.new("RGB", (MATRIX_WIDTH, MATRIX_HEIGHT))]
        durations = [1000]
        metadata = []

    return rgb_frames, durations, metadata


def image_to_rgb565(image):
    image = image.convert("RGB").resize((MATRIX_WIDTH, MATRIX_HEIGHT))
    data = bytearray()

    for y in range(MATRIX_HEIGHT):
        for x in range(MATRIX_WIDTH):
            r, g, b = image.getpixel((x, y))
            rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            data.append((rgb565 >> 8) & 0xFF)
            data.append(rgb565 & 0xFF)

    return bytes(data)


def webp_to_rgb565_frames(webp_file):
    image = Image.open(webp_file)
    rgb_frames, durations, metadata = composited_animation_frames(image)
    frames = [image_to_rgb565(frame) for frame in rgb_frames]

    if not frames:
        frames = [bytes(FRAME_BYTE_COUNT)]
        durations = [1000]

    return frames, rgb_frames, durations, metadata


def ensure_frame_cache_is_fresh():
    key = current_frame_cache_key()

    with frame_cache_lock:
        if key is None:
            if frame_cache["key"] is not None:
                reset_frame_cache()
            return

        if frame_cache["key"] == key and frame_cache["frames"]:
            if should_refresh_live_render(key, frame_cache["started_at"], frame_cache["total_duration"]):
                start_background_frame_render(key)
            return

        cached_key = frame_cache["key"]
        is_live_refresh = (
            cached_key is not None
            and frame_cache["frames"]
            and cache_key_without_refresh_bucket(cached_key) == cache_key_without_refresh_bucket(key)
            and cached_key != key
        )
        if is_live_refresh:
            start_background_frame_render(key)
            return

        if frame_cache["error_key"] == key:
            raise RuntimeError(frame_cache["error"])

    error = refresh_frame_cache_for_key(key)
    if error:
        raise RuntimeError(error)


def refresh_frame_cache_for_key(key):
    webp_file, render_started_monotonic, render_started_wall, error = render_current_webp()
    if error:
        with frame_cache_lock:
            frame_cache["error_key"] = key
            frame_cache["error"] = error
        return error

    frames, rgb_frames, durations, metadata = webp_to_rgb565_frames(webp_file)
    playback_started_at = time.monotonic()
    rendered_at = time.time()
    if is_live_render_key(key):
        playback_started_at = render_started_monotonic
        rendered_at = render_started_wall

    with frame_cache_lock:
        if current_frame_cache_key() != key:
            return None

        frame_cache["frames"] = frames
        frame_cache["rgb_frames"] = rgb_frames
        frame_cache["durations"] = durations
        frame_cache["total_duration"] = sum(durations)
        frame_cache["started_at"] = playback_started_at
        frame_cache["rendered_at"] = rendered_at
        frame_cache["key"] = key
        frame_cache["error_key"] = None
        frame_cache["error"] = None
        frame_cache["webp_file"] = webp_file
        frame_cache["metadata"] = metadata
    return None


def start_background_frame_render(key):
    global background_render_key

    if background_render_key == key:
        return

    background_render_key = key

    def refresh():
        global background_render_key

        try:
            refresh_frame_cache_for_key(key)
        finally:
            with frame_cache_lock:
                if background_render_key == key:
                    background_render_key = None

    threading.Thread(target=refresh, daemon=True).start()


def current_rgb565_frame():
    ensure_frame_cache_is_fresh()

    return cached_rgb565_frame_at(time.monotonic())


def cached_rgb565_frame_at(now):
    with frame_cache_lock:
        frames = frame_cache["frames"]
        durations = frame_cache["durations"]
        total = frame_cache["total_duration"]
        started_at = frame_cache["started_at"]
        key = frame_cache["key"]

        if len(frames) == 1 or total <= 0:
            return frames[0]

        elapsed_ms = int((now - started_at) * 1000)
        if is_live_render_key(key):
            elapsed_ms = min(elapsed_ms, total - 1)
        else:
            elapsed_ms = elapsed_ms % total

        running = 0
        for frame, duration in zip(frames, durations):
            running += duration
            if elapsed_ms < running:
                return frame

        return frames[-1]


def publish_latest_frame(frame, render_ms, interval_ms, error=None):
    if len(frame) != FRAME_BYTE_COUNT:
        print(
            f"latest-frame rejected invalid frame: bytes={len(frame)} expected={FRAME_BYTE_COUNT}",
            flush=True,
        )
        return

    with latest_frame_lock:
        latest_frame["frame"] = frame
        latest_frame["sequence"] += 1
        latest_frame["prepared_at"] = time.monotonic()
        latest_frame["render_ms"] = render_ms
        latest_frame["interval_ms"] = interval_ms
        latest_frame["error"] = error
        latest_frame_lock.notify_all()


def latest_rgb565_frame_snapshot():
    with latest_frame_lock:
        return (
            latest_frame["frame"],
            latest_frame["sequence"],
            latest_frame["prepared_at"],
            latest_frame["render_ms"],
            latest_frame["interval_ms"],
            latest_frame["error"],
        )


def ensure_frame_producer_running():
    global frame_producer_thread

    if frame_producer_thread and frame_producer_thread.is_alive():
        return

    with latest_frame_lock:
        if frame_producer_thread and frame_producer_thread.is_alive():
            return

        frame_producer_thread = threading.Thread(
            target=latest_frame_producer_loop,
            name="rgb565-latest-frame-producer",
            daemon=True,
        )
        frame_producer_thread.start()


def latest_frame_producer_loop():
    next_tick = time.monotonic()
    last_prepared_at = None
    last_stats_log = time.monotonic()
    frame_count = 0
    worst_render_ms = 0.0
    worst_interval_ms = 0.0
    spike_count = 0

    while True:
        tick_started = time.monotonic()
        render_error = None

        try:
            ensure_frame_cache_is_fresh()
            frame = cached_rgb565_frame_at(tick_started)
        except RuntimeError as error:
            frame = bytes(FRAME_BYTE_COUNT)
            render_error = str(error)

        prepared_at = time.monotonic()
        render_ms = (prepared_at - tick_started) * 1000
        interval_ms = 0.0 if last_prepared_at is None else (prepared_at - last_prepared_at) * 1000
        last_prepared_at = prepared_at

        publish_latest_frame(frame, render_ms, interval_ms, render_error)

        frame_count += 1
        worst_render_ms = max(worst_render_ms, render_ms)
        worst_interval_ms = max(worst_interval_ms, interval_ms)
        is_spike = render_ms > STREAM_SPIKE_LOG_MS or interval_ms > STREAM_SPIKE_LOG_MS
        if is_spike:
            spike_count += 1
            print(
                "rgb565 producer spike: "
                f"render_ms={render_ms:.2f} interval_ms={interval_ms:.2f} "
                f"frame_bytes={len(frame)} error={render_error or '-'}",
                flush=True,
            )

        now = time.monotonic()
        if now - last_stats_log >= STREAM_STATS_LOG_SECONDS:
            print(
                "rgb565 producer stats: "
                f"frames={frame_count} last_render_ms={render_ms:.2f} "
                f"worst_render_ms={worst_render_ms:.2f} "
                f"last_interval_ms={interval_ms:.2f} "
                f"worst_interval_ms={worst_interval_ms:.2f} "
                f"spikes={spike_count}",
                flush=True,
            )
            last_stats_log = now
            frame_count = 0
            worst_render_ms = 0.0
            worst_interval_ms = 0.0
            spike_count = 0

        next_tick += TARGET_STREAM_FRAME_SECONDS
        sleep_for = next_tick - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
        else:
            next_tick = time.monotonic()


def stream_socket_from_environ(environ):
    for key in ("gunicorn.socket", "werkzeug.socket"):
        sock = environ.get(key)
        if sock is not None:
            return key, sock
    return None, None


def configure_stream_socket(environ, remote_addr, stream_name):
    socket_key, sock = stream_socket_from_environ(environ)
    result = {
        "socket_key": socket_key or "-",
        "tcp_nodelay": "unavailable",
        "sndbuf": "unavailable",
    }

    if sock is None:
        print(
            f"{stream_name} socket options unavailable: remote={remote_addr} "
            "socket_key=- wsgi_server_socket=not-exposed",
            flush=True,
        )
        return result

    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        result["tcp_nodelay"] = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
    except OSError as error:
        result["tcp_nodelay"] = f"error:{error.__class__.__name__}:{error}"

    if STREAM_SOCKET_SNDBUF > 0:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, STREAM_SOCKET_SNDBUF)
            result["sndbuf"] = sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
        except OSError as error:
            result["sndbuf"] = f"error:{error.__class__.__name__}:{error}"
    else:
        try:
            result["sndbuf"] = sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
        except OSError as error:
            result["sndbuf"] = f"error:{error.__class__.__name__}:{error}"

    print(
        f"{stream_name} socket options: remote={remote_addr} "
        f"socket_key={result['socket_key']} tcp_nodelay={result['tcp_nodelay']} "
        f"sndbuf={result['sndbuf']} direct_passthrough=true middleware=none",
        flush=True,
    )
    return result


def stream_full_frames(stream_name, frame_provider, include_producer_stats=False):
    remote_addr = request.remote_addr
    user_agent = request.headers.get("User-Agent", "-")
    socket_options = configure_stream_socket(request.environ, remote_addr, stream_name)

    def generate():
        next_tick = time.monotonic()
        last_sent_at = None
        last_stats_log = time.monotonic()
        sent_frames = 0
        worst_write_flush_ms = 0.0
        worst_interval_ms = 0.0
        spike_count = 0

        print(
            f"{stream_name} connected: remote={remote_addr} user_agent={user_agent} "
            f"socket_key={socket_options['socket_key']}",
            flush=True,
        )

        try:
            while True:
                frame, details = frame_provider()
                frame_started = time.monotonic()
                stream_interval_ms = 0.0 if last_sent_at is None else (frame_started - last_sent_at) * 1000
                last_sent_at = frame_started

                yield frame

                flushed_at = time.monotonic()
                write_flush_ms = (flushed_at - frame_started) * 1000
                sent_frames += 1
                worst_write_flush_ms = max(worst_write_flush_ms, write_flush_ms)
                worst_interval_ms = max(worst_interval_ms, stream_interval_ms)
                is_spike = write_flush_ms > STREAM_SPIKE_LOG_MS or stream_interval_ms > STREAM_SPIKE_LOG_MS
                if is_spike:
                    spike_count += 1
                    print(
                        f"{stream_name} spike: remote={remote_addr} "
                        f"write_flush_ms={write_flush_ms:.2f} "
                        f"stream_interval_ms={stream_interval_ms:.2f} "
                        f"{details}",
                        flush=True,
                    )

                now = time.monotonic()
                if now - last_stats_log >= STREAM_STATS_LOG_SECONDS:
                    producer_suffix = f" {details}" if include_producer_stats else ""
                    print(
                        f"{stream_name} stats: remote={remote_addr} frames={sent_frames} "
                        f"last_write_flush_ms={write_flush_ms:.2f} "
                        f"worst_write_flush_ms={worst_write_flush_ms:.2f} "
                        f"last_stream_interval_ms={stream_interval_ms:.2f} "
                        f"worst_stream_interval_ms={worst_interval_ms:.2f} "
                        f"spikes={spike_count}{producer_suffix}",
                        flush=True,
                    )
                    last_stats_log = now
                    sent_frames = 0
                    worst_write_flush_ms = 0.0
                    worst_interval_ms = 0.0
                    spike_count = 0

                next_tick += TARGET_STREAM_FRAME_SECONDS
                sleep_for = next_tick - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
                else:
                    next_tick = time.monotonic()
        except GeneratorExit:
            print(f"{stream_name} disconnected: remote={remote_addr} reason=generator_exit", flush=True)
            raise
        except (BrokenPipeError, ConnectionResetError) as error:
            print(
                f"{stream_name} write exception: remote={remote_addr} "
                f"exception={error.__class__.__name__} message={error}",
                flush=True,
            )
            raise
        except OSError as error:
            print(
                f"{stream_name} socket exception: remote={remote_addr} "
                f"exception={error.__class__.__name__} errno={getattr(error, 'errno', '-')} "
                f"message={error}",
                flush=True,
            )
            raise

    return Response(
        stream_with_context(generate()),
        content_type="application/octet-stream",
        headers=rgb565_response_headers(),
        direct_passthrough=True,
    )


def rgb565_response_headers():
    return {
        "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
        "Content-Encoding": "identity",
        "X-Accel-Buffering": "no",
        "X-Matrix-Width": str(MATRIX_WIDTH),
        "X-Matrix-Height": str(MATRIX_HEIGHT),
        "X-Pixel-Format": "rgb565",
        "X-Byte-Order": "big_endian",
        "X-Frame-Bytes": str(FRAME_BYTE_COUNT),
    }


@app.route("/")
def home():
    host = request.host.split(":")[0]
    options_map = load_options()
    app_files = find_apps()
    return render_template_string(
        PAGE,
        app_files=app_files,
        current_app=current_app,
        current_app_display_name=app_display_name(current_app),
        app_display_names=app_display_names(app_files),
        host=host,
        browser_pixlet_url=BROWSER_PIXLET_URL,
        esp32_frame_url=ESP32_FRAME_URL,
        options_map=options_map,
    )


@app.route("/health")
def health():
    return jsonify({"status": "ok", "current_app": current_app, "saved_options": load_options().get(current_app, "")})


@app.route("/esp32-config")
def esp32_config():
    stream_url = ESP32_FRAME_URL.rsplit("/", 1)[0] + "/stream.rgb565"
    return jsonify({
        "frame_url": ESP32_FRAME_URL,
        "stream_url": stream_url,
        "width": MATRIX_WIDTH,
        "height": MATRIX_HEIGHT,
        "pixel_format": "rgb565",
        "byte_order": "big_endian",
        "frame_bytes": FRAME_BYTE_COUNT,
        "refresh_ms": round(TARGET_STREAM_FRAME_MS),
        "stream_fps": TARGET_STREAM_FPS,
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
    reset_frame_cache()
    ensure_frame_producer_running()
    save_state()

    return redirect(url_for("home"), code=303)


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
    try:
        ensure_frame_cache_is_fresh()
    except RuntimeError as error:
        return str(error), 500

    with frame_cache_lock:
        webp_file = frame_cache["webp_file"]

    if not webp_file:
        return "No app selected", 500

    return send_file(webp_file, mimetype="image/webp")


@app.route("/debug/frames")
def debug_frames():
    try:
        ensure_frame_cache_is_fresh()
    except RuntimeError as error:
        return jsonify({"ok": False, "error": str(error)}), 500

    debug_dir = RENDER_DIR / "debug_frames"
    debug_dir.mkdir(exist_ok=True)
    for old_frame in debug_dir.glob("frame_*.png"):
        old_frame.unlink()

    with frame_cache_lock:
        rgb_frames = [frame.copy() for frame in frame_cache["rgb_frames"]]
        metadata = list(frame_cache["metadata"])
        durations = list(frame_cache["durations"])
        total_duration = frame_cache["total_duration"]
        rendered_at = frame_cache["rendered_at"]
        webp_file = frame_cache["webp_file"]

    if not rgb_frames:
        rgb_frames = [Image.new("RGB", (MATRIX_WIDTH, MATRIX_HEIGHT))]
        durations = [1000]
        total_duration = 1000

    frame_paths = []
    for index, frame in enumerate(rgb_frames):
        output_file = debug_dir / f"frame_{index:03d}.png"
        frame.save(output_file)
        frame_paths.append(str(output_file.relative_to(BASE_DIR)))

    return jsonify({
        "ok": True,
        "frame_count": len(rgb_frames),
        "durations_ms": durations,
        "total_duration_ms": total_duration,
        "rendered_at": rendered_at,
        "webp_file": str(webp_file.relative_to(BASE_DIR)) if webp_file else None,
        "debug_dir": str(debug_dir.relative_to(BASE_DIR)),
        "frames": frame_paths,
        "metadata": metadata,
    })


@app.route("/frame.rgb565")
def frame_rgb565():
    try:
        rgb565 = current_rgb565_frame()
    except RuntimeError as error:
        return str(error), 500

    return Response(
        rgb565,
        content_type="application/octet-stream",
        headers=rgb565_response_headers(),
    )


@app.route("/stream.rgb565")
def stream_rgb565():
    ensure_frame_producer_running()

    def latest_frame_provider():
        frame, sequence, prepared_at, render_ms, producer_interval_ms, error = latest_rgb565_frame_snapshot()
        frame_age_ms = (time.monotonic() - prepared_at) * 1000
        details = (
            f"seq={sequence} producer_render_ms={render_ms:.2f} "
            f"producer_interval_ms={producer_interval_ms:.2f} "
            f"frame_age_ms={frame_age_ms:.2f} error={error or '-'}"
        )
        return frame, details

    return stream_full_frames("rgb565 stream", latest_frame_provider, include_producer_stats=True)


@app.route("/stream-test.rgb565")
def stream_test_rgb565():
    def constant_frame_provider():
        return CONSTANT_TEST_FRAME, "source=constant"

    return stream_full_frames("rgb565 stream-test", constant_frame_provider)


@app.route("/stop", methods=["POST"])
def stop_app():
    global current_app, current_app_path
    stop_pixlet()
    current_app = None
    current_app_path = None
    reset_frame_cache()
    ensure_frame_producer_running()
    save_state()
    return redirect(url_for("home"))


def start_runtime_services():
    global runtime_services_started

    if runtime_services_started:
        return

    with runtime_services_lock:
        if runtime_services_started:
            return

        start_preview_proxy()
        restore_last_app()
        ensure_frame_producer_running()
        runtime_services_started = True


if __name__ == "__main__":
    start_runtime_services()
    app.run(host="0.0.0.0", port=int(FLASK_PORT), threaded=True)
