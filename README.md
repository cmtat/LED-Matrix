# LED-Matrix

Flask dashboard for running Pixlet apps and serving the current frame to an ESP32-driven LED matrix.

## Network URLs

With the included Portainer/Docker Compose setup, the ZimaOS host is expected to be:

```text
192.168.1.252
```

The main URLs are:

```text
Dashboard:       http://192.168.1.252:5050
Pixlet preview:  http://192.168.1.252:8080
ESP32 frame:     http://192.168.1.252:5050/frame.rgb565
ESP32 stream:    http://192.168.1.252:5050/stream.rgb565
ESP32 config:    http://192.168.1.252:5050/esp32-config
```

## ESP32 Setup

Configure the ESP32 firmware to connect to the same LAN as the ZimaOS host, then read the continuous frame stream:

```text
http://192.168.1.252:5050/stream.rgb565
```

The stream yields raw RGB565 frames for a 64x32 matrix at 30 FPS by default. `/frame.rgb565` returns the same current animation frame as a single response for debug polling:

```text
Width:       64
Height:      32
Format:      RGB565
Byte order:  big endian, high byte first
Frame size:  4096 bytes
```

The endpoints return a blank frame until an app is selected, so the ESP32 can boot and connect before the dashboard is used.

The ESP32 can also fetch `/esp32-config` to discover the frame URL, stream URL, dimensions, pixel format, byte order, and suggested refresh interval.

To inspect the server-side animation extraction, open `/debug/frames`. It exports the currently cached full-canvas PNG frames to `renders/debug_frames/frame_000.png`, `frame_001.png`, and so on, and returns frame metadata including source size, tile/update extents, duration, disposal, and blend fields.

## Portainer Deploy

Use the included `docker-compose.yml`. It uses host networking so Flask and Pixlet bind directly on the ZimaOS host:

```yaml
network_mode: host
```

The important environment values are:

```text
PUBLIC_HOST=192.168.1.252
FLASK_PORT=5050
PIXLET_PORT=8080
PIXLET_INTERNAL_PORT=18080
BROWSER_PIXLET_URL=http://192.168.1.252:8080/
ESP32_FRAME_URL=http://192.168.1.252:5050/frame.rgb565
MATRIX_WIDTH=64
MATRIX_HEIGHT=32
DATA_DIR=/app/data
```

Saved app settings are written to `/app/data/app_options.json`, and the last selected app is written to `/app/data/state.json`. Both are backed by the `matrix-v2-data` Docker volume. That keeps settings across Portainer redeploys instead of storing them inside the disposable container filesystem. When the container starts, it restores the last selected app so the ESP32 can resume polling frames after a restart.

The preview on port `8080` is an autosaving wrapper. Pixlet runs internally on port `18080`, while `http://192.168.1.252:8080/` shows the Pixlet preview and automatically saves setting changes for the current app. You should no longer need to copy and paste the Pixlet URL query string into the dashboard.

After redeploying the stack, open the dashboard, run an app, then verify these URLs from another device on the LAN:

```text
http://192.168.1.252:5050
http://192.168.1.252:8080
http://192.168.1.252:5050/frame.rgb565
http://192.168.1.252:5050/esp32-config
```
