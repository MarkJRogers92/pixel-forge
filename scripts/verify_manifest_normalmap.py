"""Reproducible demonstration for the two Pixel Forge targets:

  TARGET 3  the explicit asset manifest (forge/manifest.py)
  TARGET 4  the optional normal-map pass + lighting preview (forge/normalmap.py)

No provider is called and no credit is spent. Fixtures are synthetic or the
project's own approved reference art under profiles/refs/dead-mall/.

    .venv/bin/python scripts/verify_manifest_normalmap.py --out /tmp/pf-demo

Writes the exported asset folders and the review images named in the reports.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from forge import manifest, normalmap  # noqa: E402
from forge.sprite import Sprite  # noqa: E402

REFS = ROOT / "profiles" / "refs" / "dead-mall"


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def marker_frame(marker_x: int, w: int = 16, h: int = 16) -> Sprite:
    """A synthetic frame whose marker pixel column identifies it in the sheet."""
    s = Sprite(w, h, {"k": "#101018", "n": "#ff00aa", "w": "#f6f1e4"})
    for y in range(h - 5, h - 1):
        for x in range(1, w - 1):
            s.set(x, y, "k")
    for y in range(2, h - 8):
        s.set(2, y, "w")
        s.set(w - 3, y, "w")
    s.set(marker_x, 3, "n")
    return s


def block_fixture() -> Image.Image:
    """(1) a simple shape with a known orientation."""
    arr = np.zeros((16, 16, 4), np.uint8)
    arr[4:12, 4:12] = (200, 120, 60, 255)
    return Image.fromarray(arr, "RGBA")


def staircase_fixture() -> Image.Image:
    """(2) a stepped silhouette."""
    arr = np.zeros((20, 20, 4), np.uint8)
    for i in range(5):
        arr[2 + i * 2:4 + i * 2, 2:6 + i * 2] = (120, 120, 120, 255)
    return Image.fromarray(arr, "RGBA")


def upscale(img: Image.Image, scale: int = 8) -> Image.Image:
    return img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)


# --------------------------------------------------------------------------- #
# TARGET 3
# --------------------------------------------------------------------------- #
def demo_manifest(out: Path) -> dict:
    report: dict = {"runs": {}, "ok": True}

    # (a) the approved sample: one state, our anchor contract, wrapped in a manifest
    approved = REFS / "payphone.png"
    folder = out / "manifest-payphone"
    result = manifest.run(str(approved), str(folder), direction="south", anchor="bottom-center",
                          alignment="canvas", backend="pixellab-pixflux",
                          parameters={"description": "green mall payphone", "image_size": {"width": 32, "height": 48},
                                      "direction": "south", "seed": 7},
                          normal_map=True)
    report["runs"]["approved_sample"] = result
    report["ok"] &= bool(result["verified"]["ok"])
    upscale(Image.open(folder / "sheet.png"), 8).convert("RGB").save(
        out / "pf-03-manifest-payphone-sheet-8x.png")

    # (b) the synthetic multi-frame fixture: frame ORDER is the load-bearing contract
    frames = {"f0": marker_frame(4), "f1": marker_frame(7), "f2": marker_frame(10)}
    folder_b = out / "manifest-frames"
    m = manifest.export_manifest(folder_b, frames["f0"], frames=frames,
                                 reference=manifest.reference_record(approved),
                                 animation={"name": "scan", "fps": 12.0, "loop": "forward"},
                                 generation=manifest.generation_record("pixellab-bitforge"))
    report["runs"]["synthetic_frames"] = manifest.verify_export(folder_b)
    report["ok"] &= bool(report["runs"]["synthetic_frames"]["ok"])
    report["frame_order"] = m["animations"]["scan"]["frames"]
    upscale(Image.open(folder_b / "sheet.png"), 8).convert("RGB").save(
        out / "pf-03-manifest-frames-sheet-8x.png")

    # (c) tight alignment: the recorded offset must restore registration
    folder_c = out / "manifest-tight"
    m_tight = manifest.export_manifest(folder_c, frames["f1"], alignment="tight",
                                       reference=manifest.reference_record(approved))
    report["runs"]["tight"] = manifest.verify_export(folder_c)
    report["ok"] &= bool(report["runs"]["tight"]["ok"])
    report["tight_content_bbox"] = m_tight["canvas"]["content_bbox"]
    tight = upscale(Image.open(folder_c / "sprite.png"), 8)
    canvas = upscale(frames["f1"].to_image(), 8)
    pair = Image.new("RGBA", (tight.width + canvas.width + 24, max(tight.height, canvas.height)), (26, 24, 32, 255))
    pair.alpha_composite(canvas, (0, 0))
    pair.alpha_composite(tight, (canvas.width + 24, 0))
    pair.convert("RGB").save(out / "pf-03-tight-vs-canvas-8x.png")

    # (d) an unsupported control is flagged, never simulated
    rec = manifest.generation_record("pixellab-pixflux",
                                     parameters={"description": "x", "direction": "south", "seed": 3})
    report["unsupported_example"] = rec["unsupported"]
    report["ok"] &= bool({u["name"] for u in rec["unsupported"]} == {"direction", "seed"})
    return report


# --------------------------------------------------------------------------- #
# TARGET 4
# --------------------------------------------------------------------------- #
def demo_normalmap(out: Path) -> dict:
    lights = ("top-left", "top-right", "bottom-left", "bottom-right")
    report: dict = {"fixtures": {}, "ok": True}
    fixtures = {
        "block": block_fixture(),
        "staircase": staircase_fixture(),
        "dead-mall-payphone": Image.open(REFS / "payphone.png").convert("RGBA"),
        "dead-mall-rc-car": Image.open(REFS / "rc-car.png").convert("RGBA"),
    }
    for name, src in fixtures.items():
        normal = normalmap.normal_map_image(src, strength=1.0, detail_strength=0.5,
                                            edge_treatment="clamp", convention="opengl")
        folder = out / f"normalmap-{name}"
        folder.mkdir(parents=True, exist_ok=True)
        src.save(folder / "colour.png")
        normal.save(folder / "normal-opengl.png")
        normalmap.normal_map_image(src, convention="directx").save(folder / "normal-directx.png")
        for light in lights:
            normalmap.lighting_preview(src, normal, light).save(folder / f"lit-{light}.png")
        sheet = normalmap.review_sheet(src, normal, lights, scale=max(1, 8 // max(1, src.width // 16)))
        sheet.convert("RGB").save(out / f"pf-04-normalmap-{name}-review.png")
        report["fixtures"][name] = {
            "size": [src.width, src.height],
            "normal_size": list(normal.size),
            "alpha_matches": bool(np.array_equal(np.asarray(normal)[..., 3] > 0,
                                                 np.asarray(src)[..., 3] >= 128)),
        }
        report["ok"] &= bool(report["fixtures"][name]["size"] == report["fixtures"][name]["normal_size"])
        report["ok"] &= bool(report["fixtures"][name]["alpha_matches"])

    # the convention flip, side by side, on the stepped fixture
    src = fixtures["staircase"]
    o = normalmap.normal_map_image(src, convention="opengl")
    d = normalmap.normal_map_image(src, convention="directx")
    pair = Image.new("RGBA", (o.width * 8 * 2 + 24, o.height * 8), (26, 24, 32, 255))
    pair.alpha_composite(upscale(o), (0, 0))
    pair.alpha_composite(upscale(d), (o.width * 8 + 24, 0))
    pair.convert("RGB").save(out / "pf-04-convention-opengl-left-directx-right-8x.png")

    # the lighting response must follow the light
    n = normalmap.normal_map_array(fixtures["block"])
    box = (slice(4, 12), slice(4, 12))
    lit = {}
    for light in ("left", "right"):
        img = np.asarray(normalmap.lighting_preview(fixtures["block"], n, light))[box].astype(float)
        lit[light] = [round(img[:, :4, 0].mean(), 1), round(img[:, 4:, 0].mean(), 1)]
    report["light_response_left_right_split"] = lit
    report["ok"] &= bool(lit["left"][0] > lit["left"][1] and lit["right"][1] > lit["right"][0])
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="directory for the exported assets and previews")
    args = ap.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    result = {"out": str(out), "manifest": demo_manifest(out), "normalmap": demo_normalmap(out)}
    result["ok"] = bool(result["manifest"]["ok"] and result["normalmap"]["ok"])
    (out / "pf-verify-report.json").write_text(json.dumps(result, indent=1, default=str))
    summary = {"out": str(out), "ok": result["ok"],
               "manifest_checks": {k: v["ok"] for k, v in
                                   {**result["manifest"]["runs"]["approved_sample"]["verified"]["checks"],
                                    **result["manifest"]["runs"]["synthetic_frames"]["checks"],
                                    **result["manifest"]["runs"]["tight"]["checks"]}.items()},
               "normalmap_fixtures": {k: v["size"] for k, v in result["normalmap"]["fixtures"].items()},
               "light_response": result["normalmap"]["light_response_left_right_split"]}
    print(json.dumps(summary, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
