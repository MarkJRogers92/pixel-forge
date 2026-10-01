"""Local server: serves the Piskel-based editor and the /api used by its AI Forge panel.

Binds to 127.0.0.1 only. Standard library HTTP server; no framework needed.
"""
from __future__ import annotations

import base64
import json
import threading
import traceback
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from . import artist, dsl, library, profiles, projects, qa
from .cleanup import CleanupOptions, cleanup, load_image
from .sprite import Sprite

EDITOR = Path(__file__).resolve().parent.parent / "editor" / "dest" / "prod"
MAX_BODY = 30 * 1024 * 1024
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def decode_data_url(url: str) -> bytes:
    if not url.startswith("data:image/"):
        raise ValueError("expected an image data URL")
    return base64.b64decode(url.split(",", 1)[1])


def sprite_from_data_url(url: str) -> Sprite:
    img = load_image(decode_data_url(url))
    try:
        return Sprite.from_image(img)
    except ValueError:  # too many colours: reduce without grid detection
        return cleanup(img, CleanupOptions(max_colors=48, detect_grid=False, remove_background="alpha"))[0]


def step_json(step: artist.Step) -> dict:
    return {"kind": step.kind, "png": step.sprite.to_data_url(), "notes": step.notes,
            "errors": step.errors, "qa": step.qa, "seconds": round(step.seconds, 1)}


# ------------------------------------------------------------------- jobs
def run_generate(job_id: str, body: dict) -> None:
    job = JOBS[job_id]

    def log(msg):
        job["log"].append(msg)

    try:
        from .cli import make
        base = sprite_from_data_url(body["base_png"]) if body.get("base_png") else None
        res = make(body["prompt"].strip()[:2000], int(body.get("width") or 0) or None,
                   int(body.get("height") or 0) or None, profile=body.get("profile") or None,
                   rounds=max(0, min(int(body.get("rounds", 2)), 4)),
                   artist_name=body.get("backend") or None, model=body.get("model") or None,
                   log=log, base=base, polish=body.get("polish", True),
                   use_references=body.get("use_references", True), keep_steps=True,
                   candidates=max(1, min(int(body.get("candidates", 1)), 4)),
                   checks=[c for c in body.get("checks", []) if isinstance(c, str)][:10])
        steps = res.pop("_steps")
        job["result"] = {"library_id": res["library_id"], "png": steps[-1].sprite.to_data_url(),
                         "steps": [step_json(s) for s in steps]}
        job["status"] = "done"
        log("done")
    except Exception as e:  # report every failure to the UI
        job["status"] = "error"
        job["error"] = str(e)
        job["log"].append("error: " + str(e))
        traceback.print_exc()


# ---------------------------------------------------------------- handler
class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(EDITOR), **kw)

    def log_message(self, fmt, *args):
        if "/api/jobs/" not in (args[0] if args else ""):
            super().log_message(fmt, *args)

    def send_json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        if n > MAX_BODY:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = urlparse(self.path).path
        if not path.startswith("/api/"):
            return super().do_GET()
        try:
            if path == "/api/status":
                backends = artist.available_backends()
                return self.send_json({"backends": backends,
                                       "default_backend": "api" if "api" in backends else "cli",
                                       "profiles": profiles.list_profiles()})
            if path == "/api/library":
                return self.send_json({"items": library.list_items()})
            if path.startswith("/api/library/"):
                _, _, _, item_id, name = (path.split("/") + [""])[:5]
                p = library.item_path(item_id, name or "sprite.png")
                data = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png" if p.suffix == ".png" else "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if path == "/api/projects":
                return self.send_json({"projects": projects.list_projects()})
            if path.startswith("/api/projects/") and path.endswith("/assets"):
                pid = path.split("/")[3]
                return self.send_json({"files": projects.scan(pid)})
            if path.startswith("/api/projects/") and path.endswith("/file"):
                from urllib.parse import parse_qs
                pid = path.split("/")[3]
                rel = parse_qs(urlparse(self.path).query).get("path", [""])[0]
                data = projects.file_path(pid, rel).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            if path.startswith("/api/jobs/"):
                job = JOBS.get(path.rsplit("/", 1)[-1])
                return self.send_json(job or {"status": "missing"}, 200 if job else 404)
        except Exception as e:
            return self.send_json({"error": str(e)}, 400)
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.read_json()
            if path == "/api/generate":
                if not body.get("prompt", "").strip():
                    return self.send_json({"error": "prompt is empty"}, 400)
                job_id = uuid.uuid4().hex[:12]
                with JOBS_LOCK:
                    JOBS[job_id] = {"id": job_id, "status": "running", "log": [], "result": None}
                threading.Thread(target=run_generate, args=(job_id, body), daemon=True).start()
                return self.send_json({"job": job_id})

            if path == "/api/cleanup":
                img = load_image(decode_data_url(body["image"]))
                prof = profiles.load(body.get("profile")) if body.get("use_palette") else None
                opts = CleanupOptions(
                    width=int(body["width"]) if body.get("width") else None,
                    height=int(body["height"]) if body.get("height") else None,
                    max_colors=int(body.get("max_colors", 16)),
                    palette=(prof or {}).get("palette"),
                    remove_background=body.get("remove_background", "auto"),
                    detect_grid=bool(body.get("detect_grid", True)),
                    remove_orphans=bool(body.get("remove_orphans", False)),
                    preset=body.get("preset", "standard"))
                sprite, rep = cleanup(img, opts)
                item_id, d = library.new_dir("import-" + body.get("name", "image"))
                library.save_result(d, sprite, {"kind": "import", "prompt": body.get("name", "import"),
                                                "size": [sprite.width, sprite.height], "report": rep.__dict__})
                return self.send_json({"png": sprite.to_data_url(), "report": rep.__dict__,
                                       "qa": qa.check(sprite), "library_id": item_id})

            if path == "/api/projects/add":
                return self.send_json(projects.register(body["path"], body.get("name") or None))
            if path == "/api/projects/learn":
                return self.send_json(projects.learn_style(body["project"], include=body.get("include") or None))

            if path == "/api/render":
                base = sprite_from_data_url(body["base_png"]) if body.get("base_png") else None
                res = dsl.render(body["program"], base)
                return self.send_json({"png": res.sprite.to_data_url(), "errors": res.errors,
                                       "qa": qa.check(res.sprite)})
        except Exception as e:
            traceback.print_exc()
            return self.send_json({"error": str(e)}, 400)
        self.send_json({"error": "not found"}, 404)


def serve(port: int = 9002) -> None:
    if not EDITOR.exists():
        raise SystemExit(f"editor build missing at {EDITOR}; run: cd editor && npm run build")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Pixel Forge running at http://127.0.0.1:{port}")
    httpd.serve_forever()
