"""External draft routes: PixelLab and Retro Diffusion paint a draft, it is
turned into true pixel art at the target size, and it joins the artist's own
drafts in the judging (artist.generate). A route that wins is then reviewed
and polished like any other draft.

Keys come from the environment (PIXELLAB_API_KEY, RETRODIFFUSION_API_KEY),
falling back to an interactive zsh so GUI-launched tools see ~/.zshrc. They
are never written anywhere. Each call spends credits: PixelLab one
generation, Retro Diffusion about $0.02 (rd_fast).
"""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from PIL import Image

from .cleanup import CleanupOptions, cleanup
from .sprite import Sprite

PIXELLAB_API = "https://api.pixellab.ai"
RD_API = "https://api.retrodiffusion.ai"
KEYS = {"pixellab": "PIXELLAB_API_KEY", "rd": "RETRODIFFUSION_API_KEY"}


class RouteError(RuntimeError):
    pass


def api_key(var: str) -> str:
    key = os.environ.get(var, "").strip()
    if key:
        return key
    try:
        out = subprocess.run(["zsh", "-ic", f'printf %s "${var}"'], capture_output=True, text=True, timeout=10)
        return out.stdout.strip()
    except Exception:
        return ""


def _post_json(url: str, headers: dict, body: dict, timeout: int = 180) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={**headers, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RouteError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}") from e


def _get_json(url: str, headers: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _decode(b64: str) -> Image.Image:
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")


def _to_sprite(img: Image.Image, width: int, height: int, profile: dict | None) -> Sprite:
    """The route's image as true pixel art at the target size (native art is never re-gridded)."""
    sprite, _ = cleanup(img, CleanupOptions(width=width, height=height, max_colors=24,
                                            remove_background="auto", dark_bias=0.5, accent_share=0.2))
    return sprite


def _style_words(profile: dict | None) -> str:
    if not profile:
        return ""
    notes = profile.get("notes") or profile.get("description") or ""
    return f", {notes}" if isinstance(notes, str) and notes else ""


def pixellab(prompt: str, width: int, height: int, profile: dict | None) -> Sprite:
    key = api_key(KEYS["pixellab"])
    if not key:
        raise RouteError("set PIXELLAB_API_KEY (your PixelLab API secret) to use the pixellab route")
    size = {"width": max(32, min(400, width)), "height": max(32, min(400, height))}
    res = _post_json(f"{PIXELLAB_API}/v1/generate-image-pixflux", {"Authorization": f"Bearer {key}"}, {
        "description": f"{prompt}{_style_words(profile)}, single game sprite, pixel art",
        "image_size": size, "no_background": True, "view": "high top-down",
        "outline": "single color black outline", "text_guidance_scale": 8})
    image = res.get("image") or {}
    if not image.get("base64"):
        raise RouteError(f"PixelLab returned no image: {json.dumps(res)[:200]}")
    return _to_sprite(_decode(image["base64"]), width, height, profile)


def retro_diffusion(prompt: str, width: int, height: int, profile: dict | None) -> Sprite:
    key = api_key(KEYS["rd"])
    if not key:
        raise RouteError("set RETRODIFFUSION_API_KEY to use the rd route")
    # rd_fast rejects the smallest sizes: ask at 2x (at least 64) and shrink to the target
    scale = max(2, -(-64 // max(1, min(width, height))))
    body = {"prompt": f"{prompt}{_style_words(profile)}", "prompt_style": "rd_fast__game_asset",
            "width": min(512, width * scale), "height": min(512, height * scale),
            "num_images": 1, "remove_bg": True}
    headers = {"X-RD-Token": key}
    res = _post_json(f"{RD_API}/v2/inferences", headers, body)
    deadline = time.time() + 300
    while res.get("task_id") and not res.get("base64_images") and time.time() < deadline:
        if res.get("status") in ("failed", "error"):
            raise RouteError(f"Retro Diffusion task failed: {json.dumps(res)[:200]}")
        time.sleep(3)
        res = _get_json(f"{RD_API}/v2/inferences/tasks/{res['task_id']}", headers)
        if isinstance(res.get("result"), dict):
            res = {**res, **res["result"]}
    images = res.get("base64_images") or []
    if not images:
        raise RouteError(f"Retro Diffusion returned no image: {json.dumps(res)[:200]}")
    return _to_sprite(_decode(images[0]), width, height, profile)


ROUTES: dict[str, Callable[[str, int, int, dict | None], Sprite]] = {
    "pixellab": pixellab,
    "rd": retro_diffusion,
}


def run_routes(names: list[str], prompt: str, width: int, height: int, profile: dict | None,
               workdir: Path, log: Callable[[str], None] = print) -> list[tuple[str, Sprite]]:
    """Every requested route in parallel; a failed route is logged and skipped, never fatal."""
    names = [n for n in names if n]
    unknown = [n for n in names if n not in ROUTES]
    if unknown:
        raise RouteError(f"unknown route(s) {unknown}; choose from {sorted(ROUTES)}")
    if not names:
        return []
    workdir.mkdir(parents=True, exist_ok=True)
    log(f"external routes: {', '.join(names)} (these spend credits)")
    out = []
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {n: pool.submit(ROUTES[n], prompt, width, height, profile) for n in names}
        for n, f in futures.items():
            try:
                s = f.result()
                s.save(workdir / f"route_{n}.png")
                out.append((n, s))
                log(f"route {n}: draft ready")
            except Exception as e:
                log(f"route {n} failed: {e}")
    return out
