"""Turn arbitrary images into true pixel art.

Pipeline (each step is optional and reported):
  1. bounded load            - refuse huge files before decoding
  2. background removal      - real alpha, or flood-fill the border colour
  3. grid detection          - find the pseudo-pixel size of upscaled "fake" pixel art
  4. downsample              - one real pixel per detected cell (median of cell centre)
  5. fit to target           - optional canvas size, aspect preserved, centred
  6. colour reduction        - k-means in Lab space, or snap to a fixed palette
  7. orphan cleanup          - optional removal of isolated single pixels
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from .sprite import KEY_CHARS, TRANSPARENT, Sprite, parse_hex, to_hex

MAX_INPUT_BYTES = 25 * 1024 * 1024
MAX_INPUT_PIXELS = 4096 * 4096


@dataclass
class CleanupOptions:
    width: int | None = None          # target canvas; None = keep detected size
    height: int | None = None
    max_colors: int = 16
    palette: dict[str, str] | None = None   # snap to this palette instead of k-means
    remove_background: str = "auto"   # auto | alpha | none
    detect_grid: bool = True
    remove_orphans: bool = False
    alpha_threshold: int = 128
    dark_bias: float = 1.5            # when shrinking a lot: favour dark line art
    accent_share: float = 0.0         # >0 keeps small contrasting details (0.2 for paintings)
    preset: str = "standard"         # standard | illustration


@dataclass
class CleanupReport:
    input_size: tuple[int, int] = (0, 0)
    grid: tuple[int, int] | None = None
    grid_offset: tuple[int, int] = (0, 0)
    background: str = "none"
    output_size: tuple[int, int] = (0, 0)
    colors: int = 0
    orphans_removed: int = 0
    notes: list[str] = field(default_factory=list)
    preset: str = "standard"


# ----------------------------------------------------------------- loading
def load_image(data: bytes) -> Image.Image:
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("image file too large")
    img = Image.open(io.BytesIO(data))
    w, h = img.size  # header only; not decoded yet
    if w * h > MAX_INPUT_PIXELS:
        raise ValueError(f"image {w}x{h} too large")
    return img.convert("RGBA")


# ------------------------------------------------------------ colour space
def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


# ------------------------------------------------------------- background
def remove_background(arr: np.ndarray, mode: str, thr: int, tol: float = 12.0,
                      has_alpha: bool | None = None) -> tuple[np.ndarray, str]:
    alpha = arr[..., 3]
    if mode == "none":
        return arr, "none"
    if has_alpha is None:
        has_alpha = bool((alpha < thr).any())
    if has_alpha:
        out = arr.copy()
        out[..., 3] = np.where(alpha < thr, 0, 255)
        return out, "alpha channel"
    if mode == "alpha":
        return arr, "none (image fully opaque)"
    # border flood fill of the dominant border colour
    h, w = alpha.shape
    border = np.concatenate([arr[0, :, :3], arr[-1, :, :3], arr[:, 0, :3], arr[:, -1, :3]])
    border_a = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
    border = border[border_a >= thr]
    if len(border) == 0:
        return arr, "none (empty border)"
    lab_border = rgb_to_lab(border)
    ref = np.median(lab_border, axis=0)
    close = np.linalg.norm(lab_border - ref, axis=1) < tol
    if close.mean() < 0.6:
        return arr, "none (no uniform border)"
    lab = rgb_to_lab(arr[..., :3])
    match = np.linalg.norm(lab - ref, axis=-1) < tol
    bg = np.zeros((h, w), bool)
    stack = [(0, x) for x in range(w)] + [(h - 1, x) for x in range(w)] + \
            [(y, 0) for y in range(h)] + [(y, w - 1) for y in range(h)]
    while stack:
        y, x = stack.pop()
        if bg[y, x] or not match[y, x]:
            continue
        bg[y, x] = True
        if y > 0: stack.append((y - 1, x))
        if y < h - 1: stack.append((y + 1, x))
        if x > 0: stack.append((y, x - 1))
        if x < w - 1: stack.append((y, x + 1))
    # chroma-key fringe: soft edge pixels blended with the background colour. Peel pixels
    # next to the background that are still fairly close to it (a few passes).
    near = np.linalg.norm(lab - ref, axis=-1) < tol * 3.5
    for _ in range(3):
        grow = np.zeros_like(bg)
        grow[1:] |= bg[:-1]; grow[:-1] |= bg[1:]; grow[:, 1:] |= bg[:, :-1]; grow[:, :-1] |= bg[:, 1:]
        fringe = grow & near & ~bg
        if not fringe.any():
            break
        bg |= fringe
    out = arr.copy()
    out[..., 3] = np.where(bg, 0, 255)
    return out, f"border colour {to_hex(np.clip(border[close].mean(0), 0, 255))} removed"


# -------------------------------------------------------------- grid size
def _axis_period(profile: np.ndarray, max_k: int) -> tuple[int, int, float]:
    """Best (period, offset, score) for edge energy along one axis.

    Blur smears one edge over several columns, so edges are first reduced to
    local peaks, and each lattice line accepts peaks within +-t pixels. Scores
    are normalised against the hit rate a random edge would get, otherwise
    small periods (where every column is "near" a line) always win.
    """
    n = len(profile)
    left = np.concatenate([[-1], profile[:-1]])
    right = np.concatenate([profile[1:], [-1]])
    peaks = np.where((profile >= left) & (profile > right), profile, 0.0)
    total = peaks.sum()
    if total <= 0:
        return 1, 0, 0.0
    pos = np.nonzero(peaks)[0]
    wts = peaks[pos]
    best = []
    for k in range(2, max_k + 1):
        t = 0 if k < 6 else 1
        res = np.bincount(pos % k, weights=wts, minlength=k)
        win = sum(np.roll(res, -d) for d in range(-t, t + 1))  # win[o] = mass within t of o
        o = int(win.argmax())
        s = float(win[o] / total)
        expected = (2 * t + 1) / k
        norm = (s - expected) / (1 - expected) if expected < 1 else 0.0
        best.append((k, o, norm))
    top = max(s for _, _, s in best)
    if top < 0.5:
        return 1, 0, top
    # the largest period that still explains (almost) all edges; divisors of the
    # true period explain them too, multiples of it do not
    for k, o, s in sorted(best, key=lambda x: -x[0]):
        if s >= 0.9 * top:
            return k, o, s
    return 1, 0, top


def detect_grid(arr: np.ndarray) -> tuple[int, int, int, int, float]:
    """Return (kx, ky, ox, oy, confidence). k == 1 means native pixel art."""
    lab = rgb_to_lab(arr[..., :3]) * (arr[..., 3:4] / 255.0)
    a = np.concatenate([lab, arr[..., 3:4] / 2.55], -1)
    dx = np.abs(np.diff(a, axis=1)).sum(-1)
    dy = np.abs(np.diff(a, axis=0)).sum(-1)
    # keep only strong edges so JPEG noise does not vote
    tx, ty = np.percentile(dx, 90), np.percentile(dy, 90)
    px = np.where(dx > max(tx, 8), dx, 0).sum(0)
    py = np.where(dy > max(ty, 8), dy, 0).sum(1)
    # an edge between column i and i+1 sits at boundary position i+1
    px = np.concatenate([[0], px])
    py = np.concatenate([[0], py])
    h, w = arr.shape[:2]
    kx, ox, sx = _axis_period(px, min(64, w // 4))
    ky, oy, sy = _axis_period(py, min(64, h // 4))
    # pixel art cells are square: reconcile axes
    if kx != ky:
        if abs(kx - ky) <= 1:
            k = kx if sx >= sy else ky
            kx = ky = k
        elif sx >= sy:
            ky = kx
        else:
            kx = ky
    return kx, ky, ox % kx if kx > 1 else 0, oy % ky if ky > 1 else 0, min(sx, sy)


def downsample_grid(arr: np.ndarray, kx: int, ky: int, ox: int, oy: int, thr: int) -> np.ndarray:
    h, w = arr.shape[:2]
    # only cells that are at least 3/4 inside the image; slivers at the edges are dropped
    xs = [x for x in range(ox - kx, w, kx) if min(x + kx, w) - max(x, 0) >= kx * 3 / 4]
    ys = [y for y in range(oy - ky, h, ky) if min(y + ky, h) - max(y, 0) >= ky * 3 / 4]
    out = np.zeros((len(ys), len(xs), 4), np.uint8)
    mx, my = max(kx // 4, 0), max(ky // 4, 0)
    for j, y0 in enumerate(ys):
        for i, x0 in enumerate(xs):
            y1, x1 = max(y0 + my, 0), max(x0 + mx, 0)
            y2, x2 = min(y0 + ky - my, h), min(x0 + kx - mx, w)
            if y2 <= y1 or x2 <= x1:
                continue
            cell = arr[y1:y2, x1:x2].reshape(-1, 4)
            opaque = cell[cell[:, 3] >= thr]
            if len(opaque) * 2 < len(cell):
                continue
            out[j, i, :3] = np.median(opaque[:, :3], axis=0)
            out[j, i, 3] = 255
    return out


def downscale_dominant(crop: np.ndarray, nw: int, nh: int, colors: int = 32,
                       dark_bias: float = 1.5, accent_share: float = 0.0,
                       accent_contrast: float = 35.0) -> np.ndarray:
    """Shrink a detailed illustration without smearing it.

    Averaging (BOX) turns thin dark line art into mud. Instead: reduce colours
    at full resolution, then give each output pixel the most common colour of
    its source area, with extra weight for dark colours so outlines survive.
    """
    h, w = crop.shape[:2]
    opaque = crop[..., 3] >= 128
    px = crop[..., :3][opaque]
    if len(px) > 40000:
        px = px[np.random.default_rng(1).choice(len(px), 40000, replace=False)]
    pal = kmeans_palette(px, colors)
    plab = rgb_to_lab(pal)
    lab = rgb_to_lab(crop[..., :3]).reshape(-1, 3)
    idx = np.empty(len(lab), int)
    for i in range(0, len(lab), 200000):  # chunked nearest-colour lookup
        chunk = lab[i:i + 200000]
        idx[i:i + 200000] = np.argmin(((chunk[:, None] - plab[None]) ** 2).sum(-1), 1)
    idx = idx.reshape(h, w)
    weight = 1.0 + dark_bias * np.clip((60 - plab[:, 0]) / 60, 0, 1)  # dark colours count more
    out = np.zeros((nh, nw, 4), np.uint8)
    xe = np.linspace(0, w, nw + 1).astype(int)
    ye = np.linspace(0, h, nh + 1).astype(int)
    for j in range(nh):
        for i in range(nw):
            a = opaque[ye[j]:ye[j + 1], xe[i]:xe[i + 1]]
            if a.size == 0 or a.mean() < 0.5:
                continue
            cell = idx[ye[j]:ye[j + 1], xe[i]:xe[i + 1]][a]
            raw = np.bincount(cell, minlength=len(pal))
            best = int((raw * weight).argmax())
            if accent_share > 0:
                # keep small, strongly contrasting details (cracks, buttons, lights) that
                # would never be the majority colour of their cell
                share = raw / raw.sum()
                contrast = np.linalg.norm(plab - plab[best], axis=1)
                cand = np.nonzero((share >= accent_share) & (contrast >= accent_contrast))[0]
                if len(cand):
                    best = int(cand[np.argmax(contrast[cand] * share[cand])])
            out[j, i, :3] = pal[best]
            out[j, i, 3] = 255
    return out


def fit_to(arr: np.ndarray, w: int, h: int, margin: int = 1, dark_bias: float = 1.5,
           accent_share: float = 0.0) -> np.ndarray:
    """Scale content (bbox) to fit w x h keeping aspect, centred, with a margin."""
    ys, xs = np.nonzero(arr[..., 3] > 0)
    if len(xs) == 0:
        return np.zeros((h, w, 4), np.uint8)
    crop = arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    ch, cw = crop.shape[:2]
    iw, ih = max(1, w - 2 * margin), max(1, h - 2 * margin)
    s = min(iw / cw, ih / ch)
    nw, nh = max(1, round(cw * s)), max(1, round(ch * s))
    if s < 0.5:
        small = downscale_dominant(crop, nw, nh, dark_bias=dark_bias, accent_share=accent_share)
    else:
        # NEAREST keeps edges when growing or shrinking only a little
        img = Image.fromarray(crop, "RGBA").resize((nw, nh), Image.NEAREST if s >= 1 else Image.BOX)
        small = np.asarray(img).copy()
    small[..., 3] = np.where(small[..., 3] >= 128, 255, 0)
    out = np.zeros((h, w, 4), np.uint8)
    x0, y0 = (w - nw) // 2, (h - nh) // 2
    out[y0:y0 + nh, x0:x0 + nw] = small
    return out


# ------------------------------------------------------------- quantize
def kmeans_palette(rgb: np.ndarray, k: int, seed: int = 7, iters: int = 25) -> np.ndarray:
    uniq = np.unique(rgb, axis=0)
    if len(uniq) <= k:
        return uniq
    lab = rgb_to_lab(rgb)
    rng = np.random.default_rng(seed)
    centers = [lab[rng.integers(len(lab))]]
    for _ in range(1, k):  # k-means++
        d = np.min([np.sum((lab - c) ** 2, 1) for c in centers], axis=0)
        centers.append(lab[rng.choice(len(lab), p=d / d.sum())] if d.sum() > 0 else lab[0])
    centers = np.array(centers)
    for _ in range(iters):
        lbl = np.argmin(((lab[:, None] - centers[None]) ** 2).sum(-1), 1)
        new = np.array([lab[lbl == i].mean(0) if (lbl == i).any() else centers[i] for i in range(k)])
        if np.allclose(new, centers):
            break
        centers = new
    lbl = np.argmin(((lab[:, None] - centers[None]) ** 2).sum(-1), 1)
    # use the member colour closest to each centre so palette colours are real
    out = []
    for i in range(k):
        members = rgb[lbl == i]
        if len(members):
            ml = lab[lbl == i]
            out.append(members[np.argmin(((ml - centers[i]) ** 2).sum(1))])
    return np.unique(np.array(out), axis=0)


def map_to_palette(arr: np.ndarray, pal_rgb: np.ndarray) -> np.ndarray:
    """Index of nearest palette colour (Lab) per pixel; -1 for transparent."""
    h, w = arr.shape[:2]
    idx = np.full((h, w), -1, int)
    mask = arr[..., 3] > 0
    if mask.any():
        lab = rgb_to_lab(arr[..., :3][mask])
        plab = rgb_to_lab(pal_rgb)
        idx[mask] = np.argmin(((lab[:, None] - plab[None]) ** 2).sum(-1), 1)
    return idx


def remove_orphans(idx: np.ndarray) -> tuple[np.ndarray, int]:
    """Replace opaque pixels that share colour with none of their 8 neighbours."""
    h, w = idx.shape
    out = idx.copy()
    n = 0
    for y in range(h):
        for x in range(w):
            c = idx[y, x]
            if c < 0:
                continue
            nb = [idx[yy, xx] for yy in range(max(y - 1, 0), min(y + 2, h))
                  for xx in range(max(x - 1, 0), min(x + 2, w)) if (yy, xx) != (y, x)]
            if len(nb) == 8 and c not in nb and all(v >= 0 for v in nb):
                vals, counts = np.unique(nb, return_counts=True)
                out[y, x] = vals[counts.argmax()]
                n += 1
    return out, n


# --------------------------------------------------------------- pipeline
def cleanup(img: Image.Image, opts: CleanupOptions) -> tuple[Sprite, CleanupReport]:
    if opts.preset not in ("standard", "illustration"):
        raise ValueError(f"unknown conversion preset {opts.preset!r}")
    rep = CleanupReport(input_size=img.size, preset=opts.preset)
    dark_bias, accent_share = opts.dark_bias, opts.accent_share
    if opts.preset == "illustration":
        # The hybrid painting route already uses these settings: reduce the dark
        # majority's weight and keep contrasting highlights while shrinking.
        dark_bias, accent_share = 0.5, 0.2
        rep.notes.append("illustration preset: preserve contrasting details while shrinking")
    arr = np.asarray(img.convert("RGBA")).copy()
    had_alpha = bool((arr[..., 3] < opts.alpha_threshold).any())

    # Grid detection runs on the untouched image: removing a background first
    # leaves a ragged mask edge on blurry inputs that confuses the detector.
    # Background removal afterwards also works better on the clean small image.
    if opts.detect_grid and min(arr.shape[:2]) >= 8:
        kx, ky, ox, oy, conf = detect_grid(arr)
        if kx > 1:
            ok, why = verify_grid(arr, kx, ky, ox, oy)
            if not ok:
                rep.notes.append(f"grid {kx}x{ky} guessed but rejected ({why}); treating as native pixel art")
                kx = ky = 1
        if kx > 1:
            rep.grid, rep.grid_offset = (kx, ky), (ox, oy)
            rep.notes.append(f"grid {kx}x{ky} px/cell, offset ({ox},{oy}), confidence {conf:.2f}")
            arr = downsample_grid(arr, kx, ky, ox, oy, opts.alpha_threshold)
        else:
            rep.notes.append("no pixel grid found (native pixel art or non-pixel image)")

    arr, rep.background = remove_background(arr, opts.remove_background, opts.alpha_threshold,
                                             has_alpha=had_alpha)
    arr[..., 3] =np.where(arr[..., 3] >= opts.alpha_threshold, 255, 0)
    if opts.remove_background == "auto" and (arr[..., 3] == 255).all():
        rep.notes.append("Background removal left the subject fully opaque; use a transparent "
                         "source or a flat background if this should be an isolated sprite.")
    shrunk = False
    if opts.width or opts.height:
        tw = opts.width or round(arr.shape[1] * opts.height / arr.shape[0])
        th = opts.height or round(arr.shape[0] * opts.width / arr.shape[1])
        if (tw, th) != (arr.shape[1], arr.shape[0]):
            ys, xs = np.nonzero(arr[..., 3])
            shrunk = bool(len(xs) and (xs.max() - xs.min() + 1 > max(1, tw - 2)
                                      or ys.max() - ys.min() + 1 > max(1, th - 2)))
            arr = fit_to(arr, tw, th, dark_bias=dark_bias, accent_share=accent_share)
            rep.notes.append(f"fitted to {tw}x{th}")

    mask = arr[..., 3] > 0
    if opts.palette:
        keys = list(opts.palette)
        pal = np.array([parse_hex(opts.palette[k])[:3] for k in keys], np.uint8)
        rep.notes.append(f"snapped to {len(keys)}-colour palette")
    else:
        pal = kmeans_palette(arr[..., :3][mask], max(1, min(opts.max_colors, len(KEY_CHARS)))) if mask.any() else np.zeros((0, 3), np.uint8)
        # order dark -> light so keys read like a ramp
        order = np.argsort(rgb_to_lab(pal)[:, 0]) if len(pal) else []
        pal = pal[order]
        keys = list(KEY_CHARS[:len(pal)])
    idx = map_to_palette(arr, pal) if len(pal) else np.full(arr.shape[:2], -1)

    if opts.remove_orphans:
        idx, rep.orphans_removed = remove_orphans(idx)

    h, w = idx.shape
    grid = [[keys[idx[y, x]] if idx[y, x] >= 0 else TRANSPARENT for x in range(w)] for y in range(h)]
    sprite = Sprite(w, h, {keys[i]: to_hex(pal[i]) for i in range(len(pal))}, grid)
    if opts.preset == "illustration" and shrunk:
        # Import here: polish uses cleanup's colour-space helpers. Leave native
        # pixels and recovered upscales untouched when no shrinking occurred.
        from .polish import full_polish
        sprite, polish_report = full_polish(sprite, "normal")
        rep.notes.append("illustration polish: " + ", ".join(
            f"{name} {count}" for name, count in polish_report.items()))
    sprite.palette = {k: v for k, v in sprite.palette.items() if k in sprite.used_keys()}
    rep.output_size, rep.colors = (w, h), len(sprite.palette)
    return sprite, rep


def grid_uniformity(arr: np.ndarray, kx: int, ky: int, ox: int, oy: int, thr: int = 128) -> float:
    """Share of cell-centre pixels that match their cell's median colour.

    Upscaled "fake" pixel art has flat cells (close to 1.0) even when blurred,
    because only the cell borders get smeared. Native pixel art sliced on a
    wrong grid has mixed cells and scores much lower.
    """
    h, w = arr.shape[:2]
    lab = rgb_to_lab(arr[..., :3])
    mx, my = kx // 4, ky // 4
    good = total = 0
    for y0 in range(oy, h - ky + 1, ky):
        for x0 in range(ox, w - kx + 1, kx):
            a = arr[y0 + my:y0 + ky - my, x0 + mx:x0 + kx - mx, 3]
            if a.size == 0 or (a >= thr).mean() < 1:
                continue
            cell = lab[y0 + my:y0 + ky - my, x0 + mx:x0 + kx - mx].reshape(-1, 3)
            med = np.median(cell, axis=0)
            good += int((np.linalg.norm(cell - med, axis=1) < 12).sum())
            total += len(cell)
    return good / total if total else 0.0


def exact_block_ratio(arr: np.ndarray, kx: int, ky: int, ox: int, oy: int) -> float:
    """Share of opaque cells whose pixels are all exactly identical."""
    h, w = arr.shape[:2]
    same = total = 0
    for y0 in range(oy, h - ky + 1, ky):
        for x0 in range(ox, w - kx + 1, kx):
            cell = arr[y0:y0 + ky, x0:x0 + kx].reshape(-1, 4)
            if (cell[:, 3] == 0).all():
                continue
            total += 1
            same += bool((cell == cell[0]).all())
    return same / total if total else 0.0


def verify_grid(arr: np.ndarray, kx: int, ky: int, ox: int, oy: int) -> tuple[bool, str]:
    """Guard against slicing real (native) pixel art on an imaginary grid."""
    alpha = arr[..., 3]
    soft_alpha = ((alpha > 0) & (alpha < 255)).any()
    n_colors = len(np.unique(arr.reshape(-1, 4), axis=0))
    if not soft_alpha and n_colors <= 256:
        # looks like clean pixel art: a true nearest-neighbour upscale has
        # perfectly flat cells, so demand that
        r = exact_block_ratio(arr, kx, ky, ox, oy)
        return r >= 0.9, f"only {r:.0%} of cells are flat"
    u = grid_uniformity(arr, kx, ky, ox, oy)
    return u >= 0.85, f"cell uniformity {u:.2f}"
