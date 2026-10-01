import asyncio
import json

import numpy as np
import pytest
from PIL import Image

from forge.cleanup import CleanupOptions, cleanup


def illustrated_detail():
    image = Image.new("RGBA", (48, 48), (40, 70, 100, 255))
    image.paste((245, 240, 210, 255), (16, 0, 18, 48))
    return image


def test_illustration_keeps_a_thin_contrasting_detail():
    image = illustrated_detail()
    standard, _ = cleanup(image, CleanupOptions(
        width=8, height=8, max_colors=4, detect_grid=False, remove_background="none"))
    illustrated, report = cleanup(image, CleanupOptions(
        width=8, height=8, max_colors=4, detect_grid=False,
        remove_background="none", preset="illustration"))
    normal = standard.to_rgba()
    improved = illustrated.to_rgba()
    assert not (normal[..., :3].min(axis=2) > 200).any()
    assert (improved[..., :3].min(axis=2) > 200).sum() >= 4
    assert illustrated.to_image().size == (8, 8)
    assert len(illustrated.used_keys()) <= 4
    assert set(np.unique(improved[..., 3])) <= {0, 255}
    assert report.preset == "illustration"


def test_standard_preset_preserves_existing_conversion():
    image = illustrated_detail()
    default, _ = cleanup(image, CleanupOptions(width=8, height=8, detect_grid=False))
    explicit, _ = cleanup(image, CleanupOptions(
        width=8, height=8, detect_grid=False, preset="standard"))
    assert np.array_equal(default.to_rgba(), explicit.to_rgba())


def test_illustration_leaves_native_pixels_intact():
    image = Image.new("RGBA", (8, 8))
    image.paste((40, 70, 100, 255), (2, 2, 6, 6))
    image.putpixel((3, 3), (245, 240, 210, 255))
    result, _ = cleanup(image, CleanupOptions(preset="illustration"))
    assert np.array_equal(result.to_rgba(), np.asarray(image))


def test_illustration_cleans_outline_only_when_shrinking():
    # A detailed source has bright pixels on its exterior. Converting it should
    # organize the outline, using the existing palette without filling holes.
    image = Image.new("RGBA", (48, 48))
    image.paste((30, 40, 50, 255), (4, 4, 44, 44))
    image.paste((100, 120, 140, 255), (8, 8, 40, 40))
    image.paste((245, 240, 210, 255), (4, 4, 44, 6))
    palette = {"k": "#1e2832", "b": "#64788c", "h": "#f5f0d2"}
    result, report = cleanup(image, CleanupOptions(
        width=10, height=10, detect_grid=False, palette=palette, preset="illustration"))
    # The solid edge becomes dark; isolated corner highlights remain deliberate
    # accents rather than forcing every silhouette pixel to the outline color.
    assert all(result.get(x, 1) == "k" for x in range(2, 8))
    assert result.get(1, 1) == "h" and result.get(8, 1) == "h"
    assert set(result.palette.values()) <= set(palette.values())
    assert any("polish" in note for note in report.notes)


def test_failed_auto_background_removal_is_reported():
    image = Image.new("RGB", (24, 24), "red")
    image.paste("cyan", (12, 0, 24, 24))
    _, report = cleanup(image, CleanupOptions(detect_grid=False))
    assert any("background" in note.lower() and "opaque" in note.lower()
               for note in report.notes)


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError, match="preset"):
        cleanup(illustrated_detail(), CleanupOptions(preset="typo"))


def test_mcp_pixelize_uses_illustration_preset(tmp_path, monkeypatch):
    from forge import library
    from forge.mcp_server import server
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    source = tmp_path / "source.png"
    illustrated_detail().save(source)
    result = asyncio.run(server.call_tool("forge_pixelize", {
        "image_path": str(source), "width": 8, "height": 8,
        "colors": 4, "detect_grid": False, "remove_background": "none",
        "preset": "illustration",
    }))
    assert not result.is_error
    info = json.loads(result.content[0].text)
    assert info["report"]["preset"] == "illustration"
    assert Image.open(info["png"]).size == (8, 8)


def test_cli_cleanup_uses_illustration_preset(tmp_path, capsys):
    from forge.cli import main
    source, output = tmp_path / "source.png", tmp_path / "output.png"
    illustrated_detail().save(source)
    main(["cleanup", str(source), "-W", "8", "-H", "8", "--colors", "4",
          "--background", "none", "--no-grid", "--preset", "illustration",
          "--out", str(output)])
    info = json.loads(capsys.readouterr().out)
    assert info["report"]["preset"] == "illustration"
    assert Image.open(output).size == (8, 8)


def test_http_cleanup_uses_illustration_preset(tmp_path, monkeypatch):
    import base64
    import io
    import threading
    from http.server import ThreadingHTTPServer
    from urllib.request import Request, urlopen
    from forge import library
    from forge.server import Handler
    monkeypatch.setattr(library, "ROOT", tmp_path / "library")
    buf = io.BytesIO()
    illustrated_detail().save(buf, "PNG")
    body = {"image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
            "width": 8, "height": 8, "max_colors": 4, "detect_grid": False,
            "remove_background": "none", "preset": "illustration"}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(f"http://127.0.0.1:{httpd.server_port}/api/cleanup",
                          data=json.dumps(body).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=5) as response:
            info = json.load(response)
        assert info["report"]["preset"] == "illustration"
        raw = base64.b64decode(info["png"].split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as image:
            assert image.size == (8, 8)
            assert (np.asarray(image)[..., :3].min(axis=2) > 200).sum() >= 4
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
