"""The merged generator: Aseprite-style lint checks, external draft routes
(PixelLab, Retro Diffusion) judged alongside the artist's own drafts, and the
round trip through Aseprite (layers out, edited art back in)."""
import base64
import io
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from forge import artist, aseprite, lint, qa, routes
from forge.sprite import Sprite


def sprite(rows, palette=None):
    return Sprite.from_dict({"width": len(rows[0]), "height": len(rows), "rows": rows,
                             "palette": palette or {"k": "#101018", "a": "#e0408a", "b": "#40d0c8", "c": "#f0d020"}})


def box(w=12, h=10, inner="a"):
    rows = ["k" * w] + ["k" + inner * (w - 2) + "k" for _ in range(h - 2)] + ["k" * w]
    return rows


# ------------------------------------------------------------------ lint
def test_a_clean_outlined_box_lints_clean():
    rep = lint.lint(sprite(box()))
    assert rep["passed"] is True
    assert rep["score"] == 100
    assert rep["findings"] == []


def test_lone_pixels_are_strays_and_more_than_three_is_an_error():
    rows = box(16, 12)
    rows = [r + "...." for r in rows]
    rows[2] = rows[2][:17] + "a" + rows[2][18:]
    one = lint.lint(sprite(rows))
    assert [f["check"] for f in one["findings"]] == ["strays"]
    assert one["findings"][0]["severity"] == "warning"
    assert one["findings"][0]["at"] == [17, 2]
    for y in (4, 6, 8):
        rows[y] = rows[y][:18] + "a" + rows[y][19:]
    many = lint.lint(sprite(rows))
    assert many["findings"][0]["severity"] == "error"
    assert many["passed"] is False


def test_a_gap_in_a_mostly_dark_outline_is_flagged():
    rows = box(14, 12)
    rows[5] = "a" + rows[5][1:]
    rep = lint.lint(sprite(rows))
    outline = [f for f in rep["findings"] if f["check"] == "outline"]
    assert outline and outline[0]["severity"] == "warning"
    assert outline[0]["at"] == [0, 5]


def test_a_long_straight_colour_boundary_is_banding():
    rows = box(30, 10)
    for y in range(1, 5):
        rows[y] = "k" + "b" * 28 + "k"
    rep = lint.lint(sprite(rows))
    assert any(f["check"] == "banding" for f in rep["findings"])


def test_soft_alpha_in_a_png_is_antialiasing():
    img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    for x in range(8):
        img.putpixel((x, 4), (255, 0, 0, 255))
    img.putpixel((3, 3), (255, 0, 0, 90))
    rep = lint.lint_image(img)
    assert any(f["check"] == "antialiasing" for f in rep["findings"])


def test_qa_carries_the_lint_report():
    rep = qa.check(sprite(box()))
    assert rep["lint"]["score"] == 100


# ---------------------------------------------------------------- routes
def png_b64(w, h, color=(224, 64, 138, 255)):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for y in range(h // 4, h - h // 4):
        for x in range(w // 4, w - w // 4):
            img.putpixel((x, y), color)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def test_pixellab_route_returns_a_sprite_at_the_target_size(monkeypatch):
    calls = []

    def fake_post(url, headers, body, timeout=0):
        calls.append((url, headers, body))
        return {"image": {"type": "base64", "base64": png_b64(body["image_size"]["width"], body["image_size"]["height"])}}

    monkeypatch.setattr(routes, "_post_json", fake_post)
    monkeypatch.setattr(routes, "api_key", lambda name: "test-key")
    out = routes.pixellab("a red cola machine", 32, 48, None)
    assert (out.width, out.height) == (32, 48)
    url, headers, body = calls[0]
    assert url.endswith("/v1/generate-image-pixflux")
    assert headers["Authorization"] == "Bearer test-key"
    assert body["no_background"] is True


def test_retro_diffusion_route_asks_big_and_shrinks(monkeypatch):
    calls = []

    def fake_post(url, headers, body, timeout=0):
        calls.append((url, headers, body))
        return {"base64_images": [png_b64(body["width"], body["height"])]}

    monkeypatch.setattr(routes, "_post_json", fake_post)
    monkeypatch.setattr(routes, "api_key", lambda name: "rdpk-test")
    out = routes.retro_diffusion("a red cola machine", 32, 32, None)
    assert (out.width, out.height) == (32, 32)
    url, headers, body = calls[0]
    assert url.endswith("/v2/inferences")
    assert headers["X-RD-Token"] == "rdpk-test"
    assert body["width"] >= 64 and body["remove_bg"] is True


def test_a_route_without_a_key_says_which_variable_to_set(monkeypatch):
    monkeypatch.setattr(routes, "api_key", lambda name: "")
    with pytest.raises(routes.RouteError, match="PIXELLAB_API_KEY"):
        routes.pixellab("x", 32, 32, None)


def test_run_routes_keeps_going_when_one_route_fails(monkeypatch, tmp_path):
    monkeypatch.setitem(routes.ROUTES, "pixellab", lambda p, w, h, prof: sprite(box(w, h)))

    def broken(p, w, h, prof):
        raise routes.RouteError("no credit")
    monkeypatch.setitem(routes.ROUTES, "rd", broken)
    log = []
    got = routes.run_routes(["pixellab", "rd"], "x", 12, 10, None, tmp_path, log.append)
    assert [label for label, _ in got] == ["pixellab"]
    assert (tmp_path / "route_pixellab.png").exists()
    assert any("rd failed" in line and "no credit" in line for line in log)


# ------------------------------------------- external drafts in the judging
class FakeBackend(artist.Backend):
    name = "fake"

    def __init__(self, pick):
        self.pick = pick

    def ask(self, system, text, images, workdir):
        if "judging" in text:
            n = text.split("judging ")[1].split(" ")[0]
            return "```json\n" + json.dumps({"best": self.pick, "scores": [{"id": i + 1} for i in range(int(n))]}) + "\n```"
        return "```json\n" + json.dumps({"width": 12, "height": 10, "palette": {"k": "#101018", "a": "#e0408a"},
                                         "ops": [{"op": "rect", "x": 0, "y": 0, "w": 12, "h": 10, "c": "a"}]}) + "\n```"


def test_an_external_draft_can_win_the_judging_and_is_then_reviewed(tmp_path):
    external = sprite(box(12, 10, "b"))
    req = artist.Request("box", 12, 10, rounds=0, candidates=1, polish=False,
                         external=[("pixellab", external)])
    steps = artist.generate(req, FakeBackend(pick=2), tmp_path, log=lambda m: None)
    picked = [s for s in steps if s.kind == "draft" and "picked" in s.notes]
    assert picked and "pixellab" in picked[0].notes
    assert steps[-1].sprite.rows() == external.rows()


# ---------------------------------------------------------------- aseprite
needs_aseprite = pytest.mark.skipif(aseprite.find_executable() is None, reason="Aseprite not installed")


@needs_aseprite
def test_layers_go_out_to_an_aseprite_file_and_come_back(tmp_path):
    a = sprite(box(12, 10, "a"))
    b = sprite(box(12, 10, "b"))
    pa, pb = a.save(tmp_path / "a.png"), b.save(tmp_path / "b.png")
    doc = aseprite.build_document([("final", pa, True), ("candidate 2", pb, False)], tmp_path / "s.aseprite")
    assert doc.exists()
    assert aseprite.layer_names(doc) == ["final", "candidate 2"]
    flat = aseprite.export_png(doc, tmp_path / "flat.png")
    same = lambda path, s: (np.asarray(Image.open(path).convert("RGBA")) == s.to_rgba()).all()
    assert same(flat, a)          # hidden layers stay out of the flattened art
    only_b = aseprite.export_png(doc, tmp_path / "b_out.png", layer="candidate 2")
    assert same(only_b, b)


def test_find_executable_honours_aseprite_path(monkeypatch, tmp_path):
    fake = tmp_path / "aseprite"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("ASEPRITE_PATH", str(fake))
    assert aseprite.find_executable() == str(fake)


# ------------------------------------------------- editors: both canvases
from forge import editors, library


def fake_item(tmp_path, monkeypatch):
    """A library item laid out like a finished make: final, two drafts, a route, a review step."""
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    item_id, d = library.new_dir("test box")
    final = sprite(box(12, 10, "a"))
    library.save_result(d, final, {"kind": "generate", "prompt": "test box", "size": [12, 10]})
    work = d / "work"
    work.mkdir()
    for name, inner in (("draft_1.png", "b"), ("draft_2.png", "c"), ("route_pixellab.png", "b"), ("step_1.png", "c")):
        sprite(box(12, 10, inner)).save(work / name)
    return item_id, d, final


def test_the_document_layers_are_the_final_then_every_version(tmp_path, monkeypatch):
    item_id, d, _ = fake_item(tmp_path, monkeypatch)
    assert editors.document_layers(item_id) == [
        ("final", d / "sprite.png", True),
        ("review 1", d / "work" / "step_1.png", False),
        ("draft 1", d / "work" / "draft_1.png", False),
        ("draft 2", d / "work" / "draft_2.png", False),
        ("route pixellab", d / "work" / "route_pixellab.png", False),
    ]


def test_piskel_opens_the_item_by_url(tmp_path, monkeypatch):
    item_id, _, _ = fake_item(tmp_path, monkeypatch)
    opened = []
    monkeypatch.setattr(editors, "_ensure_server", lambda port: None)
    monkeypatch.setattr(editors, "_open_url", opened.append)
    out = editors.open_item(item_id, "piskel")
    assert out["piskel"] == f"http://127.0.0.1:9002/?open={item_id}"
    assert opened == [out["piskel"]]


@needs_aseprite
def test_aseprite_gets_the_layered_document_and_edits_come_back_as_a_new_version(tmp_path, monkeypatch):
    item_id, d, final = fake_item(tmp_path, monkeypatch)
    opened = []
    monkeypatch.setattr(aseprite, "open_in_editor", lambda doc: opened.append(doc) or str(doc))
    out = editors.open_item(item_id, "aseprite")
    doc = Path(out["aseprite"])
    assert doc == d / "sprite.aseprite" and opened == [doc]
    assert aseprite.layer_names(doc)[0] == "final"

    back = editors.import_from_aseprite(doc, out_png=None, polish=False)
    assert back["library_id"] != item_id
    new = Sprite.from_image(Image.open(back["png"]))
    assert (new.to_rgba() == final.to_rgba()).all()
    assert back["qa"]["lint"]["score"] == 100
    meta = json.loads((library.ROOT / back["library_id"] / "meta.json").read_text())
    assert meta["kind"] == "aseprite-edit" and meta["source"] == str(doc)


# ------------------------------------------------ make: routes and editors
def test_make_sends_route_drafts_to_the_judging_and_opens_the_editor(tmp_path, monkeypatch):
    from forge import cli
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    seen = {}

    def fake_routes(names, prompt, w, h, prof, workdir, log):
        seen["routes"] = names
        return [("pixellab", sprite(box(w, h, "b")))]

    def fake_generate(req, backend, workdir, log):
        seen["external"] = [label for label, _ in req.external]
        final = sprite(box(req.width, req.height))
        return [artist.Step("final", {}, final, [], qa.check(final))]

    monkeypatch.setattr(routes, "run_routes", fake_routes)
    monkeypatch.setattr(artist, "generate", fake_generate)
    monkeypatch.setattr(artist, "make_backend", lambda name, model: FakeBackend(1))
    monkeypatch.setattr(editors, "open_item", lambda item_id, editor: seen.setdefault("opened", (item_id, editor)) and {"library_id": item_id, editor: "x"})
    result = cli.make("box", 12, 10, routes=["pixellab"], open_in="aseprite", log=lambda m: None)
    assert seen["routes"] == ["pixellab"]
    assert seen["external"] == ["pixellab"]
    assert seen["opened"] == (result["library_id"], "aseprite")
    assert result["opened"] == {"library_id": result["library_id"], "aseprite": "x"}


# ----------------------------------- GPT when Codex's default model is refused
REFUSED = 'ERROR: {"type":"error","status":400,"error":{"message":"The \'gpt-6.1-sol\' model is not supported when using Codex with a ChatGPT account."}}'


def test_codex_retries_a_refused_default_model_with_the_fallback(tmp_path, monkeypatch):
    import subprocess as sp
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        if "-m" not in cmd:
            return sp.CompletedProcess(cmd, 0, "", REFUSED)
        out = Path(cmd[cmd.index("-o") + 1])
        out.write_text("```json\n{}\n```")
        return sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(artist.subprocess, "run", fake_run)
    monkeypatch.delenv("FORGE_CODEX_MODEL", raising=False)
    reply = artist.CodexBackend().ask("sys", "draw", [], tmp_path)
    assert "json" in reply
    assert calls[-1][calls[-1].index("-m") + 1] == artist.CODEX_FALLBACK_MODEL


def test_the_gpt_painter_retries_too_and_says_why_when_it_still_fails(tmp_path, monkeypatch):
    import subprocess as sp
    calls = []
    monkeypatch.setattr(artist, "GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(artist.subprocess, "run",
                        lambda cmd, **kw: calls.append(cmd) or sp.CompletedProcess(cmd, 0, "", REFUSED))
    with pytest.raises(artist.ArtistError, match="not supported"):
        artist.paint_with_gpt_image("x", 32, 32, None, tmp_path / "work", log=lambda m: None)
    assert len(calls) == 2 and "-m" in calls[1]


# ------------------------------------- combine: the best feature of each draft
class CombiningBackend(artist.Backend):
    """Judges #1 best, says what is worth keeping from each, and combines when asked."""
    name = "fake"

    def __init__(self, final_pick=None):
        self.asked = []
        self.final_pick = final_pick

    def ask(self, system, text, images, workdir):
        self.asked.append(text)
        if "judging" in text:
            n = int(text.split("judging ")[1].split(" ")[0])
            best = self.final_pick if (self.final_pick and n == 2) else 1
            return "```json\n" + json.dumps({"best": best, "scores": [
                {"id": i + 1, "identity": 3, "strengths": "" if i == 0 else f"feature {i + 1}"} for i in range(n)]}) + "\n```"
        if "COMBINE" in text:
            return "```json\n" + json.dumps({"base": "current", "ops": [{"op": "points", "points": [[5, 5]], "c": "k"}]}) + "\n```"
        return "```json\n" + json.dumps({"width": 12, "height": 10, "palette": {"k": "#101018", "a": "#e0408a"},
                                         "ops": [{"op": "rect", "x": 0, "y": 0, "w": 12, "h": 10, "c": "a"}]}) + "\n```"


def test_the_combine_step_brings_in_what_the_judge_liked_from_the_losers(tmp_path):
    req = artist.Request("box", 12, 10, rounds=0, candidates=1, polish=False,
                         external=[("pixellab", sprite(box(12, 10, "b"))), ("rd", sprite(box(12, 10, "c")))])
    backend = CombiningBackend(final_pick=2)
    steps = artist.generate(req, backend, tmp_path, log=lambda m: None)
    combined = [s for s in steps if s.kind == "combine"]
    assert len(combined) == 1
    prompt = next(t for t in backend.asked if "COMBINE" in t)
    assert "#2 (pixellab): feature 2" in prompt and "#3 (rd): feature 3" in prompt
    assert "feature 2" in combined[0].notes
    assert (tmp_path / "combined.png").exists()
    assert steps[-1].sprite.get(5, 5) == "k"          # the combined version won the final judging


def test_keep_best_can_still_throw_the_combination_away(tmp_path):
    req = artist.Request("box", 12, 10, rounds=0, candidates=1, polish=False,
                         external=[("pixellab", sprite(box(12, 10, "b")))])
    steps = artist.generate(req, CombiningBackend(final_pick=1), tmp_path, log=lambda m: None)
    assert any(s.kind == "combine" for s in steps)
    assert steps[-1].sprite.get(5, 5) != "k"          # the plain winner was kept


def test_no_combine_when_asked_not_to_or_nothing_was_worth_keeping(tmp_path):
    req = artist.Request("box", 12, 10, rounds=0, candidates=1, polish=False, combine=False,
                         external=[("pixellab", sprite(box(12, 10, "b")))])
    steps = artist.generate(req, CombiningBackend(), tmp_path / "a", log=lambda m: None)
    assert not any(s.kind == "combine" for s in steps)


def test_the_combined_version_gets_its_own_aseprite_layer(tmp_path, monkeypatch):
    item_id, d, _ = fake_item(tmp_path, monkeypatch)
    sprite(box(12, 10, "c")).save(d / "work" / "combined.png")
    names = [name for name, _, _ in editors.document_layers(item_id)]
    assert names[:3] == ["final", "review 1", "combined"]
