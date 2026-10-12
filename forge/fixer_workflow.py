"""GPT reference -> free neural fixer -> conservative cleanup -> Dot -> Aseprite."""
from __future__ import annotations

import hashlib
import json
import math
import shutil
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import artist, dsl, editors, library, profiles, projects, qa, rd_fixer
from . import aseprite_ops
from .cleanup import CleanupOptions, cleanup, load_image
from .sprite import Sprite, parse_hex

WORKFLOW = "gpt-rd-neural"
STAGES = (("forge cleanup", "cleaned.png"), ("RD neural fixer", "rd_neural.png"),
          ("GPT reference (scaled)", "gpt_reference.png"))
DEFAULT_CHECKS = ["subject identifiable at 1x", "clean silhouette and dark outline",
                  "requested distinguishing features survive at game size"]


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, default=str))


def _destination(out: str | None, prompt: str) -> Path | None:
    if not out:
        return None
    dst = Path(out).expanduser()
    if dst.suffix.lower() != ".png":
        dst = dst / f"{projects.slug(prompt)}.png"
    if dst.exists() or dst.is_symlink():
        raise FileExistsError(f"{dst} exists; not overwriting")
    return dst


@contextmanager
def _stage(d: Path, meta: dict, name: str, provider: str | None = None, progress=None):
    """Persist attempted calls and failures without retaining provider error text."""
    meta["stage"] = name
    if provider:
        meta["provider_calls"][provider] += 1
    _write_json(d / "meta.json", meta)
    if progress:
        progress(name)
    try:
        yield
    except Exception as exc:
        if getattr(exc, "code", None) != "JOB_CANCELLED":
            meta.update(status="failed", failed_stage=name, error_type=type(exc).__name__)
            _write_json(d / "meta.json", meta)
        raise
    _write_json(d / "meta.json", meta)
    if progress:
        progress(name)


def _bounded_ops(ops: list) -> None:
    """Keep model-produced geometry within a small, predictable rendering budget."""
    allowed = {"points", "rect", "line", "ellipse", "poly", "stamp", "erase", "replace", "copy"}
    remaining = 8192

    def check(value, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 8:
            raise ValueError("repair requires bounded drawing operations")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value) or abs(value) > 1024:
                raise ValueError("repair requires bounded drawing coordinates")
        elif isinstance(value, str):
            if len(value) > 1024:
                raise ValueError("repair requires bounded drawing data")
        elif isinstance(value, dict):
            for child in value.values():
                check(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check(child, depth + 1)
        elif value is not None and not isinstance(value, bool):
            raise ValueError("repair requires bounded drawing data")

    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in allowed:
            raise ValueError("repair requires bounded primitive drawing operations")
        check(op)


def _review(d: Path, sprite: Sprite, checks: list[str]) -> dict:
    work = d / "work"
    entries = [("Current sprite", sprite.to_image())]
    for label, filename in STAGES[1:]:
        path = work / filename
        if path.exists():
            entries.append((label, load_image(path.read_bytes())))
    zoom = 8 if max(sprite.width, sprite.height) <= 96 else 2
    scales = (1, 2, zoom) if zoom != 2 else (1, 2)
    cell_width = max(200, sprite.width * zoom + 24)
    sheet = Image.new("RGBA", (cell_width * len(entries),
        40 + sum(sprite.height * scale + 28 for scale in scales)), (18, 17, 29, 255))
    draw = ImageDraw.Draw(sheet)
    for col, (label, image) in enumerate(entries):
        draw.text((col * cell_width + 12, 12), label, fill="white")
        y = 40
        for scale in scales:
            draw.text((col * cell_width + 8, y), f"{scale}x", fill=(155, 155, 175))
            enlarged = image.resize((sprite.width * scale, sprite.height * scale), Image.Resampling.NEAREST)
            sheet.alpha_composite(enlarged, (col * cell_width + (cell_width - enlarged.width) // 2, y + 16))
            y += sprite.height * scale + 28
    sheet_path = d / "review.png"
    sheet.save(sheet_path)
    review = {"reviewer": "dot", "status": "awaiting_visual_review", "scales": [1, 2],
              "checks": checks or list(DEFAULT_CHECKS), "sheet": str(sheet_path),
              "instructions": "Inspect the current sprite at 1x and 2x. Check identity, readability, "
                  "style and requirements. QA is measurement, not visual approval. Request a focused "
                  "repair only for a concrete problem, then review again before Aseprite/final export."}
    _write_json(d / "review.json", review)
    return review


def _finish(item_id: str, d: Path, sprite: Sprite, meta: dict, destination: Path | None,
            open_in: str | None) -> dict:
    report = qa.check(sprite, max_colors=32)
    review = _review(d, sprite, meta.get("checks") or [])
    meta.update({"status": "awaiting_visual_review", "size": [sprite.width, sprite.height],
                 "qa": report, "review": review})
    library.save_result(d, sprite, meta)
    result = {"library_id": item_id, "png": str(d / "sprite.png"), "preview": str(d / "sprite@8x.png"),
              "size": meta["size"], "status": meta["status"], "qa": report, "review": review,
              "aseprite_command": f"./forge.sh open {item_id} --editor aseprite"}
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write((d / "sprite.png").read_bytes())
        result["exported"] = str(destination)
    if open_in:
        result["opened"] = editors.open_item(item_id, open_in)
    return result


def prepare(prompt: str, width: int | None = None, height: int | None = None,
            profile: str | None = None, project: str | None = None, input_png: str | None = None,
            checks: list[str] | None = None, out: str | None = None, open_in: str | None = None,
            log=print, progress=None, aseprite_pass: dict | None = None) -> dict:
    if not prompt.strip():
        raise ValueError("a sprite brief is required")
    if open_in is not None and open_in not in editors.EDITORS:
        raise ValueError("unknown editor")
    destination = _destination(out, prompt)
    if project and not profile:
        profile = projects.get(project)["id"]
        if not (profiles.ROOT / f"{profile}.json").exists():
            projects.learn_style(project, profile)
    prof = profiles.load(profile)
    size = (prof or {}).get("size") or [32, 32]
    w, h = width if width is not None else size[0], height if height is not None else size[1]
    rd_fixer.validate_size(w, h)
    # Fail before spending the GPT allowance if the fixer cannot authenticate.
    key = rd_fixer.require_key()
    source = Path(input_png).expanduser().resolve() if input_png else None
    source_bytes = source.read_bytes() if source else None
    if source_bytes is not None:
        image = load_image(source_bytes)
        if min(image.size) < 16:
            raise ValueError("fixer input must be at least 16x16")
    item_id, d = library.new_dir(prompt)
    work = d / "work"
    work.mkdir()
    meta = {"kind": "prepare", "workflow": WORKFLOW, "prompt": prompt, "profile": profile,
            "project": project, "checks": checks or [], "status": "preparing",
            "provider_calls": {"gpt_image": 0, "rd_neural_fixer": 0, "claude": 0}}
    _write_json(d / "meta.json", meta)
    if progress:
        progress("starting", item_id)
    painting = work / "gpt_painting.png"
    if source_bytes is not None:
        painting.write_bytes(source_bytes)
        meta["source"] = str(source)
    else:
        brief = prompt + ("\nRequirements visible at game size:\n- " + "\n- ".join(checks) if checks else "")
        with _stage(d, meta, "gpt_image", "gpt_image", progress=lambda stage: progress(stage, item_id) if progress else None):
            painting = artist.paint_with_gpt_image(brief, w, h, prof, work, log, strict_output=True)
    original = painting.read_bytes()
    meta["source_sha256"] = hashlib.sha256(original).hexdigest()
    image = load_image(original)
    image.resize((w, h), Image.Resampling.NEAREST).save(work / "gpt_reference.png")
    if progress:
        progress("rd_neural_fixer", item_id)
    log("RD neural fixer: reconstructing at game size (no RD balance spent)…")
    with _stage(d, meta, "rd_neural_fixer", "rd_neural_fixer", progress=lambda stage: progress(stage, item_id) if progress else None):
        fixed, fixer_report = rd_fixer.neural_fixer(image, w, h, key=key, input_path=work / "fixer_input.png")
        fixed.save(work / "rd_neural.png")
    # The fixer has already established the grid. Do not downsample it again or polish globally.
    strict_palette = (prof or {}).get("palette") if (prof or {}).get("palette_mode") == "strict" else None
    sprite, cleanup_report = cleanup(fixed, CleanupOptions(width=w, height=h, max_colors=32,
        palette=strict_palette, remove_background="auto", detect_grid=False, remove_orphans=False))
    sprite.save(work / "cleaned.png")
    if aseprite_pass:
        pass_spec = dict(aseprite_pass)
        if pass_spec.get("palette") is None and prof:
            pass_spec["palette"] = list((prof.get("palette") or {}).values()) or None
        try:
            sprite = aseprite_ops.run_pass(sprite, pass_spec)
        except (aseprite_ops.AsepriteError, ValueError) as e:
            log(f"aseprite pass {pass_spec.get('op')} skipped: {e}")
        else:
            sprite.save(work / "passed.png")
            meta["aseprite_pass"] = pass_spec
            log(f"aseprite pass {pass_spec.get('op')} applied")
    if progress:
        progress("cleanup", item_id)
    meta.update({"fixer": fixer_report, "cleanup": cleanup_report.__dict__})
    log("Cleanup/QA ready. Dot visual review is pending; Claude was not called.")
    result = _finish(item_id, d, sprite, meta, destination, open_in)
    if progress:
        progress("awaiting_visual_review", item_id)
    return result


def repair(item_id: str, instructions: str, max_changed_pixels: int | None = None,
           out: str | None = None, open_in: str | None = None, log=print) -> dict:
    """One explicit Claude Opus patch, preserved in a new item and reviewed again by Dot."""
    if not instructions.strip():
        raise ValueError("concrete repair instructions are required")
    source = library.item_path(item_id)
    source_bytes = source.read_bytes()
    image = load_image(source_bytes)
    rd_fixer.validate_size(*image.size)
    pixels = np.asarray(image)
    if np.any((pixels[..., 3] != 0) & (pixels[..., 3] != 255)):
        raise ValueError("repair requires binary transparency; use direct authoring for partial alpha")
    if len(np.unique(pixels[pixels[..., 3] != 0], axis=0)) > 32:
        raise ValueError("repair requires at most 32 colours; source preserved")
    base = Sprite.from_image(image)
    budget = max(1, base.width * base.height // 10) if max_changed_pixels is None else max_changed_pixels
    if type(budget) is not int or not 1 <= budget <= base.width * base.height:
        raise ValueError("max_changed_pixels must be between 1 and the canvas pixel count")
    if open_in is not None and open_in not in editors.EDITORS:
        raise ValueError("unknown editor")
    parent_meta = json.loads((source.parent / "meta.json").read_text())
    prompt = parent_meta.get("prompt") or f"repair {item_id}"
    destination = _destination(out, prompt)
    new_id, d = library.new_dir(prompt + " repair")
    work = d / "work"
    work.mkdir()
    meta = {**parent_meta, "kind": "repair", "parent_library_id": item_id,
            "parent_sha256": hashlib.sha256(source_bytes).hexdigest(), "repair_instructions": instructions,
            "status": "repairing", "provider_calls": {"gpt_image": 0, "rd_neural_fixer": 0, "claude": 0}}
    _write_json(d / "meta.json", meta)
    for filename in ("gpt_painting.png", "gpt_reference.png", "fixer_input.png", "rd_neural.png", "cleaned.png"):
        original = source.parent / "work" / filename
        if original.is_file():
            shutil.copyfile(original, work / filename)
    (work / "repair_base.png").write_bytes(source_bytes)
    _review(d, base, parent_meta.get("checks") or [])
    shutil.copyfile(d / "review.png", work / "review.png")
    backend = artist.make_backend("cli", "opus")
    text = (f"Make ONE targeted pixel patch on this {base.width}x{base.height} sprite.\n"
            f"ORIGINAL BRIEF: {prompt}\nREQUESTED REPAIR: {instructions}\n"
            f"Checks: {json.dumps(parent_meta.get('checks') or [])}\n"
            f"Current sprite: {json.dumps(base.to_dict())}\nQA: {json.dumps(qa.check(base))}\n"
            f"Change at most {budget} pixels. Preserve all other pixels and existing palette entries. "
            "Output only {\"base\":\"current\",\"ops\":[...]} with focused operations; "
            "new palette keys are allowed. Do not redraw, resize or globally polish. "
            "Use only points, rect, line, ellipse, poly, stamp, erase, replace or copy operations; "
            "keep coordinates inside the canvas. "
            "If no repair is warranted output {\"done\":true}.")
    log("Claude Opus: one targeted repair pass…")
    with _stage(d, meta, "claude_repair", "claude"):
        reply = backend.ask(artist.GUIDE, text, [work / "review.png"], work)
    with _stage(d, meta, "repair_validation"):
        if len(reply.encode()) > 1024 * 1024:
            raise ValueError("repair response too large")
        (work / "reply_repair.md").write_text(reply)
        program = artist.extract_program(reply)
        if not isinstance(program, dict):
            raise ValueError("repair must be a JSON object")
        if program.get("done") is True:
            program = {"base": "current", "ops": []}
        if program.get("base") != "current" or any(k in program for k in ("rows", "grid")):
            raise ValueError("repair must patch the current sprite")
        if any(program.get(k, expected) != expected for k, expected in (("width", base.width), ("height", base.height))):
            raise ValueError("repair cannot resize the sprite")
        ops = program.get("ops", [])
        if not isinstance(ops, list) or len(ops) > 128:
            raise ValueError("repair must contain at most 128 operations")
        _bounded_ops(ops)
        palette = program.get("palette") or {}
        if not isinstance(palette, dict) or any(k in base.palette and parse_hex(v) != parse_hex(base.palette[k])
                                              for k, v in palette.items()):
            raise ValueError("repair cannot change existing palette entries")
        rendered = dsl.render(program, base)
        if rendered.errors:
            raise ValueError("repair contains invalid drawing operations")
        changed = int(np.count_nonzero(np.any(base.to_rgba() != rendered.sprite.to_rgba(), axis=2)))
        if changed > budget:
            raise ValueError(f"repair changes {changed} pixels; budget is {budget}")
        if len(rendered.sprite.used_keys()) > 32:
            raise ValueError("repair exceeds the 32-colour limit")
        rendered.sprite.save(work / "repair.png")
        _write_json(work / "repair_program.json", program)
    meta["changed_pixels"] = changed
    result = _finish(new_id, d, rendered.sprite, meta, destination, open_in)
    result["changed_pixels"] = changed
    return result
