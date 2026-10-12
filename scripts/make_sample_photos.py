"""Render the generated before/after photo pair used by the demo.

These are RENDERED images, not photographs of real food. They exist so the
demo's photo estimate can be tried without a camera and a plate of rice. The
"after" photo is deliberately taken closer and off-centre, to exercise the
plate-size normalisation. A real camera goes through the same code path.

    python scripts/make_sample_photos.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "apps" / "web" / "public" / "samples" / "photos"
W, H = 640, 480
SEED = 20261012
MASS_EXPONENT = 1.35  # this generator's assumption: mass ~ area^1.35 (between flat and mound)


def table(rng: np.random.Generator) -> np.ndarray:
    y, x = np.mgrid[0:H, 0:W]
    grain = 0.5 + 0.5 * np.sin(x / 11.0 + 3.0 * np.sin(y / 47.0) + 0.9 * np.sin(x / 63.0))
    base = np.stack([92 + 24 * grain, 66 + 18 * grain, 44 + 12 * grain], axis=-1)
    return base + rng.normal(0, 2.0, (H, W, 3))


def render(mounds: list[tuple[float, float, float]], seed: int, zoom: float, dx: float, dy: float, gain: float) -> tuple[Image.Image, float]:
    """mounds are (x, y, r) in plate-radius units. Returns the image and food area / plate area."""
    rng = np.random.default_rng(seed)
    cx, cy, pr = W / 2 + dx, H / 2 + dy, 170 * zoom
    img = Image.fromarray(np.clip(table(rng), 0, 255).astype("uint8"))
    mask = Image.new("L", (W, H), 0)
    d, md = ImageDraw.Draw(img), ImageDraw.Draw(mask)
    d.ellipse([cx - pr - 7, cy - pr - 2, cx + pr + 7, cy + pr + 12], fill=(38, 28, 20))        # contact shadow
    d.ellipse([cx - pr, cy - pr, cx + pr, cy + pr], fill=(150, 156, 160))                    # stainless thali
    d.ellipse([cx - pr * 0.9, cy - pr * 0.9, cx + pr * 0.9, cy + pr * 0.9], fill=(141, 147, 152))
    d.ellipse([cx - pr * 0.72, cy - pr * 0.72, cx + pr * 0.72, cy + pr * 0.72], outline=(160, 166, 170), width=2)
    for mx, my, mr in mounds:
        x, y, r = cx + mx * pr, cy + my * pr, mr * pr
        d.ellipse([x - r - 3, y - r + 3, x + r + 3, y + r + 6], fill=(112, 116, 118))         # shadow
        d.ellipse([x - r, y - r, x + r, y + r], fill=(236, 232, 218))
        md.ellipse([x - r, y - r, x + r, y + r], fill=255)
        d.ellipse([x - 0.6 * r, y - 0.7 * r, x + 0.35 * r, y + 0.2 * r], fill=(246, 244, 235))
        for _ in range(int(r * r / 5)):                                                        # grains
            gx, gy = x + rng.uniform(-r, r), y + rng.uniform(-r, r)
            if (gx - x) ** 2 + (gy - y) ** 2 < r * r:
                c = int(rng.uniform(214, 252))
                d.ellipse([gx - 1.6, gy - 0.9, gx + 1.6, gy + 0.9], fill=(c, c - 3, c - 14))
    arr = np.asarray(img.filter(ImageFilter.GaussianBlur(0.6))).astype(float) * gain
    arr += rng.normal(0, 2.5, arr.shape)
    share = float((np.asarray(mask) > 0).sum() / (math.pi * pr * pr))
    return Image.fromarray(np.clip(arr, 0, 255).astype("uint8")), share


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.jpg"):
        old.unlink()
    before, share_b = render([(-0.12, 0.02, 0.36), (0.26, -0.14, 0.3), (0.14, 0.3, 0.27)], SEED, zoom=1.0, dx=0, dy=6, gain=1.0)
    after, share_a = render([(-0.2, 0.22, 0.19), (0.3, -0.12, 0.12)], SEED + 1, zoom=1.22, dx=34, dy=-18, gain=0.92)
    before.save(OUT / "pair-before.jpg", quality=88)
    after.save(OUT / "pair-after.jpg", quality=88)
    ratio = share_a / share_b
    manifest = {
        "status": "GENERATED",
        "note": "Rendered demonstration images, not photographs of real food.",
        "seed": SEED,
        "before": {"file": "pair-before.jpg", "food_share_of_plate": round(share_b, 4)},
        "after": {"file": "pair-after.jpg", "food_share_of_plate": round(share_a, 4), "camera": "22% closer, off-centre, 8% darker"},
        "true_area_ratio": round(ratio, 4),
        "mass_exponent_assumed_by_generator": MASS_EXPONENT,
        "true_mass_ratio": round(ratio ** MASS_EXPONENT, 4),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
