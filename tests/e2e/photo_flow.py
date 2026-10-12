"""Before/after photo estimate, end to end in the browser (demo workspace).

    python tests/e2e/photo_flow.py http://127.0.0.1:8000

Part 1 uses the generated pair and the segmentation model (downloaded from its
public CDN, so this needs a network connection): automatic outlines, the
estimate against the generator's truth, every correction tool, and recording a
return from the estimate.
Part 2 blocks the model's CDN and checks the by-hand fallback still produces
an estimate.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "screenshots"
PHOTOS = ROOT / "apps" / "web" / "public" / "samples" / "photos"
BASE = sys.argv[1].rstrip("/")
M = json.loads((PHOTOS / "manifest.json").read_text())
READY = re.compile("Outlined automatically|Outlining by hand")


def share(page: Page) -> float:
    return float(page.locator(".editor .readout .figure").get_attribute("data-share"))


def wait_ready(page: Page, timeout: int = 150_000) -> None:
    expect(page.locator(".editor [role=status]")).to_have_text(READY, timeout=timeout)
    expect(page.locator(".editor-busy")).to_have_count(0)


def tap(page: Page, x: float, y: float, w: int = 640, h: int = 480) -> None:
    """Click a point given in the source image's pixel coordinates."""
    box = page.locator(".editor-canvas").bounding_box()
    page.mouse.click(box["x"] + x / w * box["width"], box["y"] + y / h * box["height"])
    page.wait_for_timeout(150)
    expect(page.locator(".editor-busy")).to_have_count(0, timeout=30_000)


def stroke(page: Page, pts: list[tuple[float, float]], w: int = 640, h: int = 480) -> None:
    box = page.locator(".editor-canvas").bounding_box()
    to = lambda p: (box["x"] + p[0] / w * box["width"], box["y"] + p[1] / h * box["height"])
    page.mouse.move(*to(pts[0]))
    page.mouse.down()
    for p in pts[1:]:
        page.mouse.move(*to(p), steps=6)
    page.mouse.up()
    page.wait_for_timeout(120)


def result(page: Page) -> tuple[int, int, int, int]:
    box = page.locator(".photos > .stack .readout")
    g = int(re.search(r"(\d+) g", box.locator(".figure").inner_text()).group(1))
    t = box.inner_text()
    sd = int(re.search(r"± (\d+) g", t).group(1))
    lo, hi = (int(x) for x in re.search(r"between (\d+) g .* and (\d+) g", t).groups())
    return g, sd, lo, hi


def open_plate(page: Page, token: str) -> None:
    page.goto(BASE + "/demo/serve")
    page.get_by_role("button", name=token, exact=True).click()
    page.get_by_role("button", name=re.compile(r"^Serve ")).click()
    expect(page.get_by_role("heading", name="Leftovers from two photos")).to_be_visible()


def with_model(page: Page) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    open_plate(page, "T-309")
    ref = int(page.locator(".kpi", has_text="On the plate").locator(".v").inner_text().split()[0])

    page.get_by_role("button", name="Try a generated before and after pair").click()
    expect(page.get_by_text("Before eating: check the outline")).to_be_visible()
    wait_ready(page)
    expect(page.locator(".editor [role=status]")).to_have_text(re.compile("Outlined automatically"))
    before = share(page)
    assert abs(before - 100 * M["before"]["food_share_of_plate"]) <= 4, f"before share {before}% vs rendered {M['before']['food_share_of_plate']}"
    page.locator(".editor").screenshot(path=str(SHOTS / "41-photo-before-outline.png"))
    page.get_by_role("button", name="Use this outline").click()

    expect(page.get_by_text("After eating: check the outline")).to_be_visible()
    wait_ready(page)
    after = share(page)
    assert abs(after - 100 * M["after"]["food_share_of_plate"]) <= 2.5, f"after share {after}% vs rendered {M['after']['food_share_of_plate']}"
    page.locator(".editor").screenshot(path=str(SHOTS / "42-photo-after-outline.png"))

    # ---- every correction tool, on the after photo (mounds at (312,268) r39 and (416,197) r25 in the 640x480 image)
    page.get_by_role("button", name="Not food", exact=True).click()
    tap(page, 416, 197)
    assert share(page) < after - 0.5, "a 'not food' tap should drop that mound"
    page.get_by_role("button", name="Undo").click()
    assert abs(share(page) - after) < 0.2, "undo restores the outline"

    page.get_by_role("button", name="No food here").click()
    assert share(page) == 0
    page.get_by_role("button", name="Add food", exact=True).click()
    tap(page, 312, 268)
    one = share(page)
    assert 1.5 < one <= after + 0.6, f"a tap on one mound outlines food there (the model may group both mounds): {one}%"
    tap(page, 416, 197)
    assert abs(share(page) - after) <= 1.2, f"two taps recover both mounds: {share(page)}% vs {after}%"

    page.get_by_role("button", name="Brush", exact=True).click()
    stroke(page, [(250, 330), (300, 345), (350, 330)])
    brushed = share(page)
    assert brushed > after + 0.4, "the brush adds outline"
    page.get_by_role("button", name="Eraser", exact=True).click()
    stroke(page, [(240, 330), (300, 345), (360, 330)])
    stroke(page, [(250, 322), (300, 338), (350, 322)])
    assert share(page) < brushed - 0.3, "the eraser removes outline"
    expect(page.get_by_text("adjusted by hand")).to_be_visible()

    page.get_by_role("button", name="Plate", exact=True).click()
    box = page.locator(".editor-canvas").bounding_box()
    cx, cy = box["x"] + 354 / 640 * box["width"], box["y"] + 222 / 480 * box["height"]
    page.mouse.move(cx, cy); page.mouse.down(); page.mouse.move(cx + 30, cy + 10, steps=5); page.mouse.up()
    page.wait_for_timeout(150)
    page.get_by_role("button", name="Start over").click()          # back to the automatic outline
    wait_ready(page)
    assert abs(share(page) - after) <= 0.6, "start over reproduces the automatic outline"
    page.get_by_role("button", name="Use this outline").click()

    # ---- the estimate against the generator's truth
    g, sd, lo, hi = result(page)
    truth = ref * M["true_mass_ratio"]
    print(f"  model: before {before}% after {after}% -> {g} g ± {sd} (range {lo}-{hi}); generated truth {truth:.1f} g of {ref} g")
    assert lo - 2 <= truth <= hi + 2, "the generator's truth lies between the flat and mound assumptions"
    assert abs(g - truth) <= 2 * sd, f"{g} vs {truth}"
    page.locator(".photos").screenshot(path=str(SHOTS / "43-photo-result.png"))

    page.get_by_role("button", name=re.compile(r"^Use \d+ g as the returned amount")).click()
    expect(page.get_by_role("button", name=re.compile(r"Photo estimate ± \d+ g"))).to_be_visible()
    expect(page.get_by_label("Edible food left in grams")).to_have_value(str(g))
    page.get_by_role("button", name="Enough", exact=True).click()
    page.get_by_role("button", name="Had enough").click()
    page.get_by_role("button", name="Save return").click()
    expect(page.get_by_text("This plate updated the estimate.")).to_be_visible()
    assert not errors, errors[:3]


def by_hand(page: Page) -> None:
    """No access to the model: the feature must still work, by hand."""
    page.route(re.compile(r"cdn\.jsdelivr\.net|huggingface\.co"), lambda r: r.abort())
    open_plate(page, "T-415")
    page.get_by_label("Upload the before photo").set_input_files(str(PHOTOS / "pair-before.jpg"))
    wait_ready(page, 60_000)
    expect(page.locator(".editor [role=status]")).to_have_text(re.compile("Outlining by hand"))
    expect(page.get_by_text("Automatic outlining is not available")).to_be_visible()
    # place the plate outline by its handle, then check the colour guess is in the right region
    page.get_by_role("button", name="Plate", exact=True).click()
    box = page.locator(".editor-canvas").bounding_box()
    px = lambda x, y: (box["x"] + x / 640 * box["width"], box["y"] + y / 480 * box["height"])
    # the default outline is too big for this plate: pull both handles in to the rim (radius 170 px, centre (320, 246))
    for start, end in (((320 + 211, 240), (320 + 170, 240)), ((320, 240 + 211), (320, 240 + 170)), ((320, 300), (320, 306))):
        page.mouse.move(*px(*start)); page.mouse.down(); page.mouse.move(*px(*end), steps=4); page.mouse.up()
        page.wait_for_timeout(150)
    b = share(page)
    assert 8 <= b <= 45, f"by-hand colour guess for the before photo: {b}%"
    page.get_by_role("button", name="Use this outline").click()

    page.get_by_label("Upload the after photo").set_input_files(str(PHOTOS / "pair-after.jpg"))
    wait_ready(page, 60_000)
    page.get_by_role("button", name="No food here").click()
    page.get_by_role("button", name="Add food", exact=True).click()   # magic-wand fill without the model
    tap(page, 312, 268)
    tap(page, 416, 197)
    a = share(page)
    assert 1 < a < 20, f"by-hand after share {a}%"
    page.get_by_role("button", name="Use this outline").click()
    g, sd, lo, hi = result(page)
    print(f"  by hand: before {b}% after {a}% -> {g} g ± {sd}")
    assert 0 < g < 181 and lo <= g <= hi


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        for name, fn in (("with the model", with_model), ("by hand, model blocked", by_hand)):
            ctx = browser.new_context(viewport={"width": 1366, "height": 1000})
            page = ctx.new_page()
            page.set_default_timeout(20_000)
            print(name)
            fn(page)
            ctx.close()
        browser.close()
    print("PHOTO FLOW PASSED")


if __name__ == "__main__":
    main()
