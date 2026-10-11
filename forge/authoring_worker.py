"""Private fixed renderer entry point. Validated JSON only, no filesystem writes."""
from __future__ import annotations

import base64
import json
import signal
import sys

from .authoring_render import decode_png, encode, render_pixels
from .authoring_schema import AuthoringError, DEADLINE_SECONDS, strict_json, validate_request


def main():
    # Backstop if the parent itself disappears; this process cannot outlive its
    # fixed render budget and never publishes files on its own.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.setitimer(signal.ITIMER_REAL, DEADLINE_SECONDS)
    try:
        raw = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
        if len(raw) > 16 * 1024 * 1024:
            raise AuthoringError("LIMIT_EXCEEDED", "worker_packet")
        packet = strict_json(raw)
        request = validate_request(packet["kind"], packet["request"])
        base = decode_png(base64.b64decode(packet["source_png"], validate=True)) if packet["source_png"] else None
        image = render_pixels(request, base)
        output = encode(image, request, packet["profile"])
        output["png"] = base64.b64encode(output["png"]).decode("ascii")
        output["preview"] = base64.b64encode(output["preview"]).decode("ascii")
        print(json.dumps(output, allow_nan=False))
    except AuthoringError as exc:
        print(json.dumps(exc.result()))
    except Exception:
        print(json.dumps(AuthoringError("STORAGE_FAILURE", "renderer").result()))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    main()
