"""V0.9 studio background image generator (isolated, zero-cost, no API calls).

Abstract premium financial-studio backdrop: deep navy/charcoal vertical
gradient, warm key-light glow, soft bokeh accents, floor sheen, vignette.
No numbers, charts, text, faces, or distracting elements.

Usage:
    PYTHONPATH=. uv run python scripts/v09_studio_bg.py
"""

import os

from PIL import Image, ImageDraw, ImageFilter

OUTPUT = "output/v09_studio_bg.png"
W, H = 720, 1280


def main() -> int:
    base_top = (10, 18, 38)      # deep navy
    base_bottom = (24, 30, 48)   # charcoal-navy
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / (H - 1)
        # gentle ease + slight warm lift in upper third (key light zone)
        r = int(base_top[0] + (base_bottom[0] - base_top[0]) * t)
        g = int(base_top[1] + (base_bottom[1] - base_top[1]) * t)
        b = int(base_top[2] + (base_bottom[2] - base_top[2]) * t)
        for x in range(W):
            px[x, y] = (r, g, b)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # warm key-light wash, upper-left (behind where avatar head sits)
    d.ellipse([-320, -180, 620, 620], fill=(64, 84, 130, 110))
    d.ellipse([-180, -80, 420, 460], fill=(96, 120, 170, 70))
    # cool rim accent, right edge
    d.ellipse([480, 260, 900, 900], fill=(40, 90, 130, 60))
    # floor sheen, lower third
    d.rectangle([0, 880, W, H], fill=(120, 140, 190, 26))
    d.ellipse([80, 950, 640, 1150], fill=(150, 170, 220, 30))

    # soft bokeh discs (defocused studio lights), low alpha
    bokeh = [
        (120, 700, 26, 70), (600, 560, 18, 60), (540, 980, 30, 55),
        (180, 880, 14, 60), (660, 820, 12, 55), (90, 420, 16, 50),
        (640, 180, 14, 45), (300, 1080, 20, 50),
    ]
    for x, y, r, a in bokeh:
        d.ellipse([x - r, y - r, x + r, y + r],
                  fill=(170, 190, 235, a))

    layer = layer.filter(ImageFilter.GaussianBlur(radius=18))
    img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")

    # vignette (darken edges slightly for premium focus)
    vig = Image.new("L", (W, H), 0)
    vd = ImageDraw.Draw(vig)
    vd.rectangle([0, 0, W, H], fill=0)
    # build radial-ish vignette via blurred mask
    mask = Image.new("L", (W, H), 255)
    md = ImageDraw.Draw(mask)
    md.ellipse([-140, -80, W + 140, H + 80], fill=200)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=120))
    import PIL.ImageChops as Chops
    # multiply image by mask/255
    img = Image.composite(
        img,
        Image.new("RGB", (W, H), (4, 6, 14)),
        mask.point(lambda v: 255 - (255 - v) // 3),
    )

    os.makedirs("output", exist_ok=True)
    img.save(OUTPUT)
    print(f"wrote {OUTPUT} {W}x{H}")
    # content guardrails: assert no text layers (we drew none) and file valid
    assert os.path.getsize(OUTPUT) > 10_000, "bg file suspiciously small"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
