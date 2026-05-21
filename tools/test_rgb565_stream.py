#!/usr/bin/env python3
import argparse
import time
import urllib.request


def read_exact(response, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = response.read(remaining)
        if not chunk:
            raise EOFError(f"stream ended with {remaining} bytes left in frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def main():
    parser = argparse.ArgumentParser(description="Measure raw RGB565 stream frame cadence.")
    parser.add_argument("url", help="stream URL, for example http://192.168.1.252:5050/stream.rgb565")
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--frame-bytes", type=int, default=4096)
    args = parser.parse_args()

    request = urllib.request.Request(args.url, headers={"Accept-Encoding": "identity"})
    intervals = []
    read_times = []

    with urllib.request.urlopen(request, timeout=15) as response:
        print(f"status={response.status} content_type={response.headers.get('Content-Type')}")
        print(f"x_accel_buffering={response.headers.get('X-Accel-Buffering')}")
        previous_end = None

        for index in range(args.frames):
            started = time.monotonic()
            frame = read_exact(response, args.frame_bytes)
            ended = time.monotonic()

            if len(frame) != args.frame_bytes:
                raise RuntimeError(f"frame {index} had {len(frame)} bytes")

            read_ms = (ended - started) * 1000
            read_times.append(read_ms)
            if previous_end is not None:
                intervals.append((ended - previous_end) * 1000)
            previous_end = ended

    if intervals:
        print(
            "interval_ms "
            f"avg={sum(intervals) / len(intervals):.2f} "
            f"min={min(intervals):.2f} max={max(intervals):.2f}"
        )
    print(
        "read_ms "
        f"avg={sum(read_times) / len(read_times):.2f} "
        f"min={min(read_times):.2f} max={max(read_times):.2f}"
    )


if __name__ == "__main__":
    main()
