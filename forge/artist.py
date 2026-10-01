"""Claude as the pixel artist.

generate():  request -> Claude writes a sprite program -> render -> QA
             -> (review rounds) Claude looks at the upscaled render and
             returns an improved program -> ... -> best sprite.

Backends:
  cli  - the local `claude` command (uses the logged-in Claude subscription)
  api  - Anthropic Messages API (needs ANTHROPIC_API_KEY)
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import dsl, polish, qa
from .sprite import Sprite, preview_image

GUIDE = (Path(__file__).parent / "artist_guide.md").read_text()

DEFAULT_MODELS = {"cli": "opus", "api": "claude-opus-5-5"}


class ArtistError(RuntimeError):
    pass


# ------------------------------------------------------------------ backends
class Backend:
    name = "base"

    def ask(self, system: str, text: str, images: list[Path], workdir: Path) -> str:
        raise NotImplementedError


class CliBackend(Backend):
    """Runs `claude -p`. Images are placed in workdir and read with the Read tool."""
    name = "claude"

    def __init__(self, model: str | None = None, timeout: int = 600):
        self.model = model or DEFAULT_MODELS["cli"]
        self.timeout = timeout
        self.exe = shutil.which("claude") or str(Path.home() / ".local/bin/claude")

    def ask(self, system, text, images, workdir):
        if images:
            listing = "\n".join(f"- {p.name}" for p in images)
            text = (f"First use the Read tool to look at these image files in the current "
                    f"directory:\n{listing}\n\n{text}")
        cmd = [self.exe, "-p", text, "--output-format", "json", "--model", self.model,
               "--system-prompt", system, "--strict-mcp-config", "--no-session-persistence",
               "--disable-slash-commands", "--setting-sources", "",
               "--tools", "Read" if images else ""]
        try:
            proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise ArtistError(f"claude CLI timed out after {self.timeout}s")
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            raise ArtistError(f"claude CLI failed: {(proc.stderr or proc.stdout)[-600:]}")
        if out.get("is_error"):
            msg = out.get("result", "unknown error")
            if "authenticate" in msg.lower() or "oauth" in msg.lower():
                msg += " — run `claude` in a terminal and use /login, then try again."
            raise ArtistError(f"claude CLI: {msg}")
        return out.get("result", "")


# Codex's configured default model is used (gpt-6.1-sol since 2026-10-01). An out-of-date
# Codex CLI refuses models it does not know yet ("not supported ... ChatGPT account": run
# `codex update`); rather than fail the whole run, retry once with a model that works.
CODEX_FALLBACK_MODEL = "gpt-5.5"
_REFUSED = "not supported when using Codex with a ChatGPT account"


def _codex_fallback() -> str:
    return os.environ.get("FORGE_CODEX_MODEL") or CODEX_FALLBACK_MODEL


class CodexBackend(Backend):
    """GPT via the local `codex exec` command (uses the ChatGPT/Codex login)."""
    name = "gpt"

    def __init__(self, model: str | None = None, timeout: int = 900):
        self.model = model
        self.timeout = timeout
        self.exe = shutil.which("codex") or str(Path.home() / ".local/bin/codex")

    def ask(self, system, text, images, workdir):
        out_file = workdir / f"codex_reply_{int(time.time() * 1000)}.md"
        prompt = (f"{system}\n\n---\n\n{text}\n\nDo not run commands or edit files; "
                  f"just answer with your reasoning and the ```json program.")
        def run(model):
            cmd = [self.exe, "exec", "--skip-git-repo-check", "-s", "read-only",
                   "--color", "never", "-o", str(out_file)]
            if model:
                cmd += ["-m", model]
            for p in images:
                cmd += ["-i", str(p)]
            cmd += ["--", prompt]
            try:
                return subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                                      timeout=self.timeout, stdin=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                raise ArtistError(f"codex timed out after {self.timeout}s")
        proc = run(self.model)
        if _REFUSED in (proc.stderr or "") + (proc.stdout or "") and not self.model:
            self.model = _codex_fallback()
            proc = run(self.model)
        if not out_file.exists() or not out_file.read_text().strip():
            tail = (proc.stderr or proc.stdout)[-600:]
            if "login" in tail.lower() or "auth" in tail.lower():
                tail += " — run `codex login` in a terminal, then try again."
            raise ArtistError(f"codex failed: {tail}")
        return out_file.read_text()


class ApiBackend(Backend):
    name = "api"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, model: str | None = None, timeout: int = 600):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise ArtistError("ANTHROPIC_API_KEY is not set")
        self.model = model or DEFAULT_MODELS["api"]
        self.timeout = timeout

    def ask(self, system, text, images, workdir):
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                 "data": base64.b64encode(p.read_bytes()).decode()}}
                   for p in images]
        content.append({"type": "text", "text": text})
        body = json.dumps({"model": self.model, "max_tokens": 32000, "system": system,
                           "messages": [{"role": "user", "content": content}]}).encode()
        req = urllib.request.Request(self.URL, body, {
            "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise ArtistError(f"API {e.code}: {e.read()[:400].decode(errors='replace')}")
        return "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")


def available_backends() -> dict[str, str]:
    out = {}
    if shutil.which("claude") or (Path.home() / ".local/bin/claude").exists():
        out["cli"] = "claude command-line (your Claude login)"
    if shutil.which("codex") or (Path.home() / ".local/bin/codex").exists():
        out["codex"] = "GPT via codex command-line (your ChatGPT login)"
    if os.environ.get("ANTHROPIC_API_KEY"):
        out["api"] = "Anthropic API key"
    return out


BACKENDS = {"cli": CliBackend, "claude": CliBackend, "codex": CodexBackend, "gpt": CodexBackend,
            "api": ApiBackend}


def make_backend(name: str | None = None, model: str | None = None) -> Backend:
    name = name or ("api" if os.environ.get("ANTHROPIC_API_KEY") else "cli")
    if name not in BACKENDS:
        raise ArtistError(f"unknown artist {name!r}; choose from {sorted(BACKENDS)}")
    return BACKENDS[name](model)


# ------------------------------------------------------------------ parsing
def extract_program(text: str) -> dict:
    """Pull the last JSON object out of a reply (fenced or bare)."""
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates = blocks[::-1] or []
    if not candidates:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            candidates = [text[start:end + 1]]
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    raise ArtistError("reply contained no valid JSON program")


# ------------------------------------------------------------------ request
@dataclass
class Request:
    prompt: str
    width: int = 32
    height: int = 32
    profile: dict | None = None
    rounds: int = 2                     # review rounds after the first draft
    base: Sprite | None = None          # existing sprite to refine
    references: list[Path] = field(default_factory=list)
    polish: bool = True                 # automatic noise cleanup at the end
    retouch: bool = False               # edits must paint on top of `base` (hybrid mode)
    candidates: int = 1                 # parallel drafts; the judge picks the best
    keep_best: bool = True              # final judging: a worse late round never wins
    checks: list[str] = field(default_factory=list)   # acceptance checklist for reviews/judging
    # drafts painted elsewhere (routes.py: PixelLab, Retro Diffusion), judged with the artist's own
    external: list = field(default_factory=list)       # [(label, Sprite)]
    combine: bool = True     # after judging, bring the best feature of each loser into the winner


@dataclass
class Step:
    kind: str                # draft | review
    program: dict
    sprite: Sprite
    errors: list[str]
    qa: dict
    notes: str = ""
    seconds: float = 0.0


def _profile_text(profile: dict | None) -> str:
    if not profile:
        return "No style profile: choose a small, cohesive palette that suits the subject."
    parts = [f"STYLE PROFILE: {profile.get('name', '')}"]
    if profile.get("style"):
        parts.append(profile["style"])
    if profile.get("palette"):
        mode = profile.get("palette_mode", "prefer")
        rule = ("You MUST use only these colours (same keys and hex values)." if mode == "strict"
                else "Prefer these colours; add a few more only if the subject truly needs them.")
        parts.append(f"{rule}\n{json.dumps(profile['palette'])}")
    if profile.get("ramps"):
        parts.append(f"Suggested ramps: {json.dumps(profile['ramps'])}")
    return "\n".join(parts)


def _rows_block(s: Sprite) -> str:
    return json.dumps(s.to_dict(), separators=(",", ":"))


def _draft_text(req: Request, ref_names: list[str]) -> str:
    t = [f"Create a {req.width}x{req.height} pixel art sprite.", f"REQUEST: {req.prompt}", "", _profile_text(req.profile)]
    if ref_names:
        t.append("\nThe attached reference image(s) show the target style (and/or subject). "
                 "Match their proportions, palette feel, outline and shading approach. Do not copy them.")
    if req.base is not None:
        t.append("\nSTARTING SPRITE (edit this rather than starting over; use \"base\": \"current\" "
                 "if you only need to change parts):\n" + _rows_block(req.base))
    t.append("\nThink briefly (silhouette, palette ramps, light direction, key details), "
             "then output the complete sprite program as one ```json block.")
    return "\n".join(t)


def _review_text(req: Request, step: Step, round_no: int, total: int) -> str:
    return "\n".join([
        f"REVIEW ROUND {round_no}/{total}"
        + (" (FINAL: spend it on lighting, texture and hand-placed highlights)" if round_no == total else "")
        + ". The image render.png is your current sprite, upscaled "
        f"8x on a grey checkerboard (checkerboard = transparent). Judge it at that size AND imagine it at 1x.",
        f"ORIGINAL REQUEST ({req.width}x{req.height}): {req.prompt}",
        _profile_text(req.profile),
        "", "YOUR CURRENT PROGRAM:",
        (json.dumps(step.program, separators=(",", ":")) if step.program.get("base") != "current"
         else "(the last change was a patch; the rows below are the current state)"),
        "", "RENDERED RESULT (rows):", _rows_block(step.sprite),
        "", f"RENDER ERRORS: {step.errors or 'none'}",
        f"AUTOMATED QA: {json.dumps(step.qa)}",
        "", "List the 3 most important visual problems (readability of the silhouette, proportions, "
            "subject accuracy, shading/light consistency, outline, noise, stray pixels). "
            "Then output an improved program as one ```json block. For local fixes prefer "
            "{\"base\": \"current\", \"ops\": [...]} with pixel-level edits; for structural problems "
            "output a complete new program. If the sprite is already excellent, output "
            "{\"done\": true} instead.",
    ])


def _write_preview(s: Sprite, path: Path) -> Path:
    preview_image(s, scale=8 if max(s.width, s.height) <= 64 else 4).convert("RGB").save(path)
    return path


VARIATIONS = [
    "",
    "Take a bolder approach: stronger silhouette, more dramatic lighting and contrast.",
    "Take a different approach from the obvious one: rethink proportions, camera emphasis "
    "and colour accents while still matching the request exactly.",
    "Aim for maximum readability at 1x: fewer, larger shapes and very clear key details.",
]

RUBRIC = (
    "Score each numbered candidate 0-4 on:\n"
    "- identity: is it clearly the requested thing, with every requested detail visible?\n"
    "- readability: does it read instantly at 1x and 2x on the dark background (clean "
    "silhouette, no noise, no mush)?\n"
    "- style: does it match the style profile / references (palette feel, outline, shading)?\n"
    "- structure: coherent construction and perspective, no broken or merged parts?\n"
    "0 = unusable, 1 = major defects, 2 = needs substantial fixes, 3 = usable with minor fixes, "
    "4 = production quality. Judge what you SEE, not what was intended.\n"
    "For every candidate also name in 'strengths' the one specific, visible feature worth "
    "keeping even if it loses (e.g. 'the glowing pink marquee', 'plush toys readable through "
    "the glass'). Only name a feature that SERVES the request and its must-satisfy checks: a "
    "feature that contradicts them (neat hanging clothes on a rack that must look knocked "
    "over) is a defect, not a strength. Leave it empty if nothing qualifies.\n"
    "Reply with a short comparison, then one ```json block: "
    '{"scores": [{"id": 1, "identity": 0, "readability": 0, "style": 0, "structure": 0, '
    '"defects": "...", "strengths": "..."}], "best": 1}'
)


def contact_sheet(sprites: list[Sprite], path: Path) -> Path:
    """Numbered candidates at 1x and 2x on a dark game-like floor, plus a zoomed view."""
    from PIL import Image, ImageDraw
    w = max(s.width for s in sprites)
    h = max(s.height for s in sprites)
    zoom = 6 if max(w, h) <= 64 else 3
    cell_w = max(w * zoom, w * 3 + 24) + 24
    cell_h = h * zoom + h * 2 + 48
    sheet = Image.new("RGBA", (cell_w * len(sprites), cell_h), (34, 30, 44, 255))
    d = ImageDraw.Draw(sheet)
    for i, s in enumerate(sprites):
        x0 = i * cell_w + 12
        d.text((x0, 4), f"#{i + 1}", fill=(255, 235, 120, 255))
        sheet.alpha_composite(preview_image(s, zoom).convert("RGBA"), (x0, 20))
        y1 = 28 + h * zoom
        sheet.alpha_composite(s.to_image(1), (x0, y1 + h // 2))           # 1x
        sheet.alpha_composite(s.to_image(2), (x0 + w + 12, y1))            # 2x
    sheet.convert("RGB").save(path)
    return path


def judge(req: Request, sprites: list[Sprite], backend: Backend, workdir: Path,
          label: str, log: Callable[[str], None]) -> tuple[int, dict]:
    """Ask the artist model to score candidates; returns (best index, parsed result)."""
    if len(sprites) == 1:
        return 0, {}
    sheet = contact_sheet(sprites, workdir / f"judge_{label}.png")
    checks = "".join(f"\n- {c}" for c in req.checks)
    text = (f"You are judging {len(sprites)} candidate sprites for this request "
            f"({req.width}x{req.height}): {req.prompt}\n{_profile_text(req.profile)}"
            + (f"\nMust satisfy:{checks}" if checks else "")
            + f"\n\nThe image {sheet.name} shows each candidate numbered, zoomed, and below it at "
              f"1x and 2x on a dark game background.\n\n{RUBRIC}")
    try:
        reply = backend.ask("You are a strict, experienced pixel-art director.", text, [sheet], workdir)
        (workdir / f"reply_judge_{label}.md").write_text(reply)
        res = extract_program(reply)
        scores = {int(sc["id"]): sum(int(sc.get(k, 0)) for k in ("identity", "readability", "style", "structure"))
                  for sc in res.get("scores", [])}
        best = int(res.get("best") or max(scores, key=scores.get)) - 1
        if not 0 <= best < len(sprites):
            raise ValueError("best out of range")
        log(f"judge ({label}): scores {scores} -> #{best + 1}")
        return best, res
    except Exception as e:  # never lose the run because judging failed
        log(f"judge ({label}) failed ({e}); falling back to QA warnings")
        return min(range(len(sprites)), key=lambda i: len(qa.check(sprites[i])["warnings"])), {}


def _draft(req, backend, workdir, idx, text, images, log) -> Step:
    t0 = time.time()
    for attempt in range(2):
        reply = backend.ask(GUIDE, text, images, workdir)
        (workdir / f"reply_draft_{idx}_{attempt}.md").write_text(reply)
        try:
            program = extract_program(reply)
            break
        except ArtistError:
            if attempt == 1:
                raise
            text += "\n\nYour previous reply had no valid JSON block. Output ONLY the ```json program."
    if req.retouch:
        program["base"] = "current"
    res = dsl.render(program, req.base)
    res.sprite.save(workdir / f"draft_{idx + 1}.png")
    return Step("draft", program, res.sprite, res.errors, qa.check(res.sprite), seconds=time.time() - t0)


def generate(req: Request, backend: Backend, workdir: Path,
             log: Callable[[str], None] = print) -> list[Step]:
    """Best-of-N: parallel drafts -> judge -> review rounds on the winner -> keep the best."""
    from concurrent.futures import ThreadPoolExecutor

    workdir.mkdir(parents=True, exist_ok=True)
    refs = []
    for i, p in enumerate(req.references):
        dst = workdir / f"reference_{i + 1}.png"
        shutil.copy(p, dst)
        refs.append(dst)

    text = _draft_text(req, [r.name for r in refs])
    if req.checks:
        text += "\n\nMUST SATISFY (checked by a reviewer):" + "".join(f"\n- {c}" for c in req.checks)
    images = list(refs)
    if req.base is not None:
        images.append(_write_preview(req.base, workdir / "start.png"))
        text = text.replace("STARTING SPRITE", "STARTING SPRITE (also shown in start.png)")

    n = max(1, min(req.candidates, len(VARIATIONS)))
    log(f"asking {backend.name} for {n} draft{'s' if n > 1 else ''}…")
    texts = [text + (f"\n\n{VARIATIONS[i]}" if VARIATIONS[i] else "") for i in range(n)]
    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(_draft, req, backend, workdir, i, texts[i], images, log) for i in range(n)]
        drafts = []
        for i, f in enumerate(futures):
            try:
                drafts.append(f.result())
            except Exception as e:
                log(f"draft {i + 1} failed: {e}")
    # external routes compete on equal terms; a winner is then reviewed as a patch on its pixels
    labels = [f"{backend.name} draft {i + 1}" for i in range(len(drafts))]
    for label, ext in req.external:
        drafts.append(Step("candidate", {"base": "current", "ops": [], "source": label}, ext, [],
                           qa.check(ext), notes=f"route {label}"))
        labels.append(label)
    if not drafts:
        raise ArtistError("every draft failed")
    for d in drafts:
        d.kind = "candidate" if len(drafts) > 1 else "draft"
    log(f"{len(drafts)} draft(s) rendered" + (f" ({len(req.external)} from routes)" if req.external else ""))

    steps: list[Step] = list(drafts)
    best_i, verdict = judge(req, [d.sprite for d in drafts], backend, workdir, "drafts", log)
    chain = [drafts[best_i]]           # the winner and its review rounds
    if len(drafts) > 1:
        steps.append(Step("draft", drafts[best_i].program, drafts[best_i].sprite, drafts[best_i].errors,
                          drafts[best_i].qa, notes=f"picked candidate #{best_i + 1} ({labels[best_i]})"))
    drafts[best_i].sprite.save(workdir / "step_0.png")

    if req.combine and len(drafts) > 1:
        combined = _combine(req, drafts, labels, best_i, verdict, backend, workdir, refs, log)
        if combined is not None:
            chain.append(combined)
            steps.append(combined)

    for r in range(1, req.rounds + 1):
        cur = chain[-1]
        preview = _write_preview(cur.sprite, workdir / "render.png")
        t0 = time.time()
        log(f"review round {r}/{req.rounds}: {backend.name} is looking at the render…")
        text = _review_text(req, cur, r, req.rounds)
        if req.checks:
            text += "\n\nMUST SATISFY:" + "".join(f"\n- {c}" for c in req.checks)
        if req.retouch:
            text += ("\n\nRETOUCH MODE: this sprite came from a painting. Keep its texture; answer "
                     "only with {\"base\": \"current\"} and small corrective ops. Do not output a "
                     "complete new program.")
        if refs:
            text += ("\n\nThe reference_*.png images show the target style and detail level. "
                     "Compare your render against them: match their richness of lighting and "
                     "texture, not just their palette.")
        try:
            reply = backend.ask(GUIDE, text, [preview] + refs, workdir)
        except ArtistError as e:
            log(f"review round {r} failed ({e}); keeping the best so far")
            break
        (workdir / f"reply_review_{r}.md").write_text(reply)
        try:
            program = extract_program(reply)
        except ArtistError:
            log("review reply had no JSON; keeping previous version")
            continue
        if program.get("done"):
            log("the artist is satisfied with the sprite")
            break
        if req.retouch:
            program["base"] = "current"
        base = cur.sprite if program.get("base") == "current" else None
        if base is not None:
            program.setdefault("width", cur.sprite.width)
            program.setdefault("height", cur.sprite.height)
        res = dsl.render(program, base)
        notes = reply.split("```")[0].strip()[:1500]
        step = Step("review", program, res.sprite, res.errors, qa.check(res.sprite),
                    notes=notes, seconds=time.time() - t0)
        chain.append(step)
        steps.append(step)
        res.sprite.save(workdir / f"step_{r}.png")
        log(f"round {r} applied ({len(res.errors)} op errors)")

    # polish every contender, then keep the best one (a round that made it worse loses)
    contenders = []
    for st in chain:
        sp = polish.full_polish(st.sprite, "normal")[0] if req.polish else st.sprite
        contenders.append(sp)
    pick = len(contenders) - 1
    if len(contenders) > 1 and req.keep_best:
        pick, _ = judge(req, contenders, backend, workdir, "final", log)
        if pick != len(contenders) - 1:
            log(f"kept version {pick} (a later round scored lower)")
    final = contenders[pick]
    steps.append(Step("final", {"base": "current", "ops": [], "note": f"version {pick}, polished"},
                      final, [], qa.check(final),
                      notes=f"best of {len(contenders)} versions" + (" (polished)" if req.polish else "")))
    final.save(workdir / "final.png")
    return steps


def _combine(req: Request, drafts: list[Step], labels: list[str], best_i: int, verdict: dict,
             backend: Backend, workdir: Path, refs: list[Path], log) -> Step | None:
    """One sprite from all of them: the winner, plus the feature the judge liked in each loser.

    The combination is only a contender: it is reviewed like the winner and the final
    keep-best judging can still choose the plain winner, so combining never lowers quality.
    """
    wanted = []
    for sc in verdict.get("scores", []):
        try:
            i = int(sc.get("id", 0)) - 1
        except (TypeError, ValueError):
            continue
        feature = str(sc.get("strengths") or "").strip()
        if 0 <= i < len(drafts) and i != best_i and feature:
            wanted.append(f"- from #{i + 1} ({labels[i]}): {feature}")
    if not wanted:
        log("combine: the judge saw nothing in the other drafts worth bringing in")
        return None
    sheet = workdir / "judge_drafts.png"
    if not sheet.exists():
        contact_sheet([d.sprite for d in drafts], sheet)
    winner = drafts[best_i]
    start = _write_preview(winner.sprite, workdir / "start.png")
    text = "\n".join([
        f"COMBINE ({req.width}x{req.height}): {req.prompt}",
        _profile_text(req.profile),
        f"{sheet.name} shows all {len(drafts)} candidates, numbered. The STARTING SPRITE (start.png) is "
        f"candidate #{best_i + 1}, the judge's pick. Keep its silhouette, structure and proportions, and "
        f"bring in these features from the others, redrawn to fit it (copy what you see, do not invent):",
        *wanted,
        "The result must read as ONE coherent sprite: one light direction, one outline, one palette "
        "(merge near-identical colours), no seams where parts came from.",
        "Skip any listed feature that would break the request or a MUST SATISFY check: the winner's "
        "reading of the request comes first, the borrowed features second.",
        "", "STARTING SPRITE (rows):", _rows_block(winner.sprite),
        "", 'Reply with your plan, then one ```json block: a {"base": "current", "ops": [...]} patch for '
            "local additions, or a complete new program if the features need a redraw.",
    ] + (["", "MUST SATISFY:" + "".join(f"\n- {c}" for c in req.checks)] if req.checks else []))
    t0 = time.time()
    log(f"combine: bringing in {len(wanted)} feature(s) from the other drafts…")
    try:
        reply = backend.ask(GUIDE, text, [sheet, start] + refs, workdir)
        (workdir / "reply_combine.md").write_text(reply)
        program = extract_program(reply)
    except ArtistError as e:
        log(f"combine failed ({e}); continuing with the winner")
        return None
    if req.retouch:
        program["base"] = "current"
    base = winner.sprite if program.get("base") == "current" else None
    if base is not None:
        program.setdefault("width", winner.sprite.width)
        program.setdefault("height", winner.sprite.height)
    res = dsl.render(program, base)
    res.sprite.save(workdir / "combined.png")
    notes = "combined: " + "; ".join(w[2:] for w in wanted)
    return Step("combine", program, res.sprite, res.errors, qa.check(res.sprite), notes=notes,
                seconds=time.time() - t0)


# ------------------------------------------------------- hybrid: GPT paints
GENERATED_DIR = Path.home() / ".codex" / "generated_images"


def paint_with_gpt_image(prompt: str, width: int, height: int, profile: dict | None,
                         workdir: Path, log: Callable[[str], None] = print,
                         timeout: int = 900) -> Path:
    """Ask Codex's image tool (ChatGPT login) for a pixel-art painting; return the PNG path."""
    workdir.mkdir(parents=True, exist_ok=True)
    style = (profile or {}).get("style", "")
    scale = max(1, round(1024 / max(width, height)))
    brief = (
        f"Use your image generation tool to create exactly one image, then stop.\n"
        f"Subject: {prompt}\n"
        f"It is a game sprite that will be reduced to {width}x{height} pixels, so draw it as "
        f"clean, bright, readable pixel art at roughly {width}x{height} logical pixels "
        f"(each pixel shown as a {scale}x{scale} block), aspect ratio {width}:{height}.\n"
        f"Style: late-SNES / GBA quality, strong dark outline, clear silhouette, bold light and "
        f"shadow with a top-left light, 16-32 colours, bright light sources and screens.\n"
        f"{('Project style: ' + style) if style else ''}\n"
        f"The whole object is visible and centred with a small margin. Background: plain flat "
        f"solid magenta (#ff00ff) or transparent. No text, no letters, no logos, no scenery, "
        f"no shadow on the ground.\n"
        f"After the image is generated, do not edit, inspect or regenerate it. Reply only DONE.")
    exe = shutil.which("codex") or str(Path.home() / ".local/bin/codex")
    started = time.time()
    log("GPT is painting the base image…")

    def paint(model):
        cmd = [exe, "exec", "--skip-git-repo-check", "-s", "read-only", "--color", "never"]
        cmd += (["-m", model] if model else []) + ["--", brief]
        try:
            return subprocess.run(cmd, cwd=workdir, capture_output=True, text=True,
                                  timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ArtistError("GPT image generation timed out")
    proc = paint(None)
    said = (proc.stderr or "") + (proc.stdout or "")
    if _REFUSED in said:
        log(f"codex refused its default model; retrying with {_codex_fallback()}")
        proc = paint(_codex_fallback())
        said = (proc.stderr or "") + (proc.stdout or "")
    new = [p for p in GENERATED_DIR.glob("*/*.png") if p.stat().st_mtime >= started - 2]
    if not new:
        errors = [line for line in said.splitlines() if "ERROR" in line or "error" in line.lower()]
        raise ArtistError("GPT did not produce an image: " + (errors[-1][:300] if errors else
                          "no error reported (is `codex login` done and image generation enabled?)"))
    src = max(new, key=lambda p: p.stat().st_mtime)
    dst = workdir / "gpt_painting.png"
    shutil.copy(src, dst)
    used = next((line.split(":", 1)[1].strip() for line in said.splitlines() if line.startswith("model:")), "?")
    log(f"GPT painting ready ({src.name}, model {used})")
    return dst
