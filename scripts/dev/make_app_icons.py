"""Draw the installable-app icons (the manifest's icons, the iPhone home-screen icon).

    python -m scripts.dev.make_app_icons

Writes into src/assets/img/ (commit the PNGs; the build only copies them):

    app-icon-192.png, app-icon-512.png                  "any": a rounded square, transparent corners
    app-icon-maskable-192.png, app-icon-maskable-512.png "maskable": full bleed, the grapes inside the
                                                         80 % safe circle, so Android can cut any shape
    apple-touch-icon-180.png                            iPhone / iPad: full bleed, no transparency
                                                         (iOS rounds the corners itself)

The picture: the site hero's navy -> NETA-blue gradient (main.css .gv-hero) with a warm glow and
the site's grape-cluster mark (src/_includes/icons/grapes.svg) in the hero's "vine light" gold.
No text: at launcher size (48 px) words can't be read. Everything is drawn at 4x and scaled down,
so the edges stay crisp. Only Pillow + numpy (both already installed for the content sync).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[2] / "src" / "assets" / "img"
SS = 4  # supersampling factor

# .gv-hero background: linear-gradient(135deg, #0f172a 0%, #0b2f55 38%, #025a94 72%, #0170b9 100%)
STOPS = [(0.0, (0x0F, 0x17, 0x2A)), (0.38, (0x0B, 0x2F, 0x55)), (0.72, (0x02, 0x5A, 0x94)), (1.0, (0x01, 0x70, 0xB9))]
GOLD = (0xFF, 0xD7, 0x9A)        # .gv-hero-title em — "vine light" gold
GOLD_DEEP = (0xE8, 0xA8, 0x4E)
GOLD_HI = (0xFF, 0xF1, 0xD6)
LEAF = (0x8F, 0xD0, 0x8A)        # --c-vine (dark theme)
STEM = (0xD9, 0xB0, 0x72)

# grapes.svg (viewBox 24): the eight grapes, back row first
GRAPES = [(9, 8.5), (13.5, 8.5), (7, 12.5), (11.2, 12.5), (15.5, 12.3), (9.2, 16.4), (13.5, 16.4), (11.3, 20.2)]
R = 2.2
CENTER = (11.4, 12.6)   # visual centre of stem + leaf + cluster in the 24-unit box


def gradient(size: int) -> Image.Image:
    y, x = np.mgrid[0:size, 0:size].astype(np.float64)
    t = (x + y) / (2 * (size - 1))
    out = np.zeros((size, size, 3))
    for (t0, c0), (t1, c1) in zip(STOPS, STOPS[1:]):
        m = (t >= t0) & (t <= t1)
        k = ((t - t0) / (t1 - t0))[m][:, None]
        out[m] = np.array(c0) * (1 - k) + np.array(c1) * k
    # a warm glow behind the grapes (the hero's string lights)
    cx, cy, rad = size * 0.5, size * 0.47, size * 0.46
    d = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) / rad
    a = np.clip(1 - d, 0, 1) ** 1.8 * 0.30
    out = out * (1 - a[..., None]) + np.array((255, 196, 110)) * a[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def bezier(p0, p1, p2, p3, n=24):
    pts = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return pts


def draw_mark(img: Image.Image, box: float) -> None:
    """The grape cluster, `box` px for the 24-unit viewBox, centred on the image."""
    size = img.size[0]
    k = box / 24
    ox, oy = size / 2 - CENTER[0] * k, size / 2 - CENTER[1] * k
    P = lambda x, y: (ox + x * k, oy + y * k)  # noqa: E731

    # soft shadow under the cluster, so the gold reads on the lighter blue corner too
    shadow = Image.new("L", img.size, 0)
    sd = ImageDraw.Draw(shadow)
    for gx, gy in GRAPES:
        cx, cy = P(gx + 0.25, gy + 0.45)
        sd.ellipse([cx - R * k, cy - R * k, cx + R * k, cy + R * k], fill=150)
    shadow = shadow.filter(ImageFilter.GaussianBlur(k * 0.9))
    img.paste(Image.new("RGB", img.size, (4, 14, 34)), (0, 0), shadow)

    d = ImageDraw.Draw(img)
    # leaf: grapes.svg "M12 4c1.5-1.8 4-2.2 6-1.2-.6 2.1-2.6 3.4-4.8 3.2", closed back to the stem
    leaf = bezier((12, 4), (13.5, 2.2), (16, 1.8), (18, 2.8)) + bezier((18, 2.8), (17.4, 4.9), (15.4, 6.2), (13.2, 6.0))
    d.polygon([P(*p) for p in leaf], fill=LEAF)
    vein = bezier((12.6, 4.6), (14.2, 3.6), (15.8, 3.3), (17.2, 3.2), 12)
    d.line([P(*p) for p in vein], fill=(0x5E, 0xA8, 0x5A), width=max(1, round(k * 0.35)), joint="curve")
    # stem
    w = k * 1.5
    x0, y0 = P(12, 2.1)
    x1, y1 = P(12, 6.6)
    d.line([(x0, y0), (x1, y1)], fill=STEM, width=round(w))
    d.ellipse([x0 - w / 2, y0 - w / 2, x0 + w / 2, y0 + w / 2], fill=STEM)
    # grapes: deep rim, gold body, a light highlight up and to the left
    for gx, gy in GRAPES:
        cx, cy = P(gx, gy)
        r = R * k
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GOLD_DEEP)
        r2 = r * 0.86
        d.ellipse([cx - r2 - r * 0.06, cy - r2 - r * 0.08, cx + r2 - r * 0.06, cy + r2 - r * 0.08], fill=GOLD)
        hr = r * 0.30
        hx, hy = cx - r * 0.38, cy - r * 0.40
        d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], fill=GOLD_HI)


def render(size: int, *, mark: float, rounded: bool, alpha: bool) -> Image.Image:
    big = size * SS
    img = gradient(big)
    draw_mark(img, mark * big)
    if rounded:
        mask = Image.new("L", (big, big), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1], radius=round(big * 0.22), fill=255)
        rgba = img.convert("RGBA")
        rgba.putalpha(mask)
        img = rgba
    elif alpha:
        img = img.convert("RGBA")
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # "any": the mark fills ~64 % of the square; maskable: ~56 %, well inside the safe circle
    # (radius 40 %) — the leaf tip, the farthest point, sits ~28 % from the centre.
    jobs = {
        "app-icon-192.png": dict(size=192, mark=0.64, rounded=True, alpha=True),
        "app-icon-512.png": dict(size=512, mark=0.64, rounded=True, alpha=True),
        "app-icon-maskable-192.png": dict(size=192, mark=0.56, rounded=False, alpha=False),
        "app-icon-maskable-512.png": dict(size=512, mark=0.56, rounded=False, alpha=False),
        "apple-touch-icon-180.png": dict(size=180, mark=0.60, rounded=False, alpha=False),
    }
    for name, kw in jobs.items():
        size = kw.pop("size")
        im = render(size, **kw)
        im.save(OUT / name, optimize=True)
        print(f"{name}: {im.size[0]}x{im.size[1]} {im.mode} {(OUT / name).stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
