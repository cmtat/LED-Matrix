from flask import Flask, Response, jsonify, redirect, render_template_string, request, send_file, url_for
import json
import subprocess
from pathlib import Path
from PIL import Image
from urllib.parse import parse_qsl, urlencode, urlparse
import os

PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "192.168.1.252")
FLASK_PORT = os.environ.get("FLASK_PORT", "5050")
PIXLET_PORT = os.environ.get("PIXLET_PORT", "8080")
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

    pairs = parse_qsl(raw_options, keep_blank_values=True)
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
        ["pixlet", "serve", "--host", "0.0.0.0", "--port", PIXLET_PORT, str(full_path)],
        cwd=BASE_DIR,
    )


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

    options = load_options()
    normalized = normalize_options(raw_options)

    if normalized:
        options[app_path] = normalized
    else:
        options.pop(app_path, None)

    save_options(options)
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
    restore_last_app()
    app.run(host="0.0.0.0", port=int(FLASK_PORT))
