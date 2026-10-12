"""Browser walkthrough of the public demo, with assertions and screenshots.

    python tests/e2e/walkthrough.py http://127.0.0.1:8000            # local
    python tests/e2e/walkthrough.py https://<cloudfront-domain>      # deployed
    python tests/e2e/walkthrough.py <url> --video                    # also record a silent screen video

Uses the installed Chrome. Screenshots go to docs/screenshots/.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "screenshots"
BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "http://127.0.0.1:8000"
VIDEO = "--video" in sys.argv
PAUSE = 1400 if VIDEO else 0


def shot(page: Page, name: str, full: bool = True) -> None:
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(250)
    if VIDEO:  # do not disturb the recording
        return
    # a sticky header repeats in the middle of a full-page capture; pin it for the shot only
    style = page.add_style_tag(content=".topbar{position:static!important}.toast{visibility:hidden!important}")
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)
    style.evaluate("e => e.remove()")
    print("  shot", name)


def beat(page: Page, ms: int | None = None) -> None:
    """Hold on screen when recording, long enough to narrate over (about 2.5 minutes in total)."""
    if PAUSE:
        page.wait_for_timeout(int((ms or PAUSE) * 2.7))


def desktop(page: Page) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

    page.goto(BASE + "/")
    expect(page.get_by_role("heading", level=1)).to_contain_text("Serve a better first portion")
    beat(page, 2500)
    shot(page, "01-landing")
    page.get_by_role("link", name="Serve a plate in the demo").first.click()

    # ---- Serve: the diner who was served three scoops and left about one, six times
    expect(page.get_by_text("Demo · simulated data")).to_be_visible()
    page.get_by_role("button", name="T-104", exact=True).click()
    expect(page.get_by_text("Suggested first portion")).to_be_visible()
    expect(page.get_by_text("2 scoops").first).to_be_visible()
    beat(page, 2500)
    shot(page, "02-serve-suggestion")

    # ---- a new diner: serve, record the return, watch the next suggestion change
    page.get_by_role("button", name="T-309", exact=True).click()
    expect(page.get_by_text("No history for this diner yet")).to_be_visible()
    first = page.locator(".kpi .v.xl").inner_text()
    beat(page, 2000)
    page.get_by_role("button", name=re.compile(r"^Serve ")).click()
    expect(page.get_by_text("Plate came back")).to_be_visible()
    beat(page)
    page.get_by_label("Edible food left in grams").fill("95")
    page.get_by_role("button", name="Enough", exact=True).click()
    page.get_by_role("button", name="Had enough").click()
    beat(page)
    shot(page, "03-serve-return-form")
    page.get_by_role("button", name="Save return").click()
    expect(page.get_by_text("This plate updated the estimate.")).to_be_visible()
    expect(page.get_by_text("Saved in this browser's demo sandbox")).to_be_visible()
    after = page.locator(".beforeafter > div").nth(2).inner_text()
    assert first not in after, f"next portion did not change: {first!r} -> {after!r}"
    beat(page, 3000)
    shot(page, "04-serve-learning")

    # saved state survives a refresh (demo: this browser's sandbox)
    page.reload()
    page.get_by_role("button", name="T-309", exact=True).click()
    expect(page.get_by_text("Came back")).to_be_visible()

    # ---- the diner who always finishes and wants more
    page.get_by_role("button", name="T-221", exact=True).click()
    expect(page.get_by_text("Includes plates where the diner said they wanted more.")).to_be_visible()
    expect(page.get_by_text("4 scoops").first).to_be_visible()
    beat(page, 2500)
    shot(page, "05-serve-wanted-more")
    # operator override: the diner asks for less, and seconds stay available
    page.get_by_role("button", name="Less").click()
    expect(page.get_by_text("Diner's chosen first portion")).to_be_visible()
    page.get_by_role("button", name=re.compile(r"^Serve 3½ scoops")).click()
    page.get_by_role("button", name="+ ½ scoop").click()
    expect(page.get_by_text(re.compile(r"seconds \d+ g"))).to_be_visible()
    beat(page)
    shot(page, "06-serve-override-seconds")

    # ---- Learn: same empty plate, different evidence
    page.get_by_role("link", name="Learn").click()
    expect(page.get_by_role("heading", name="Same empty plate, different evidence")).to_be_visible()
    expect(page.get_by_text("What the model believes about T-221")).to_be_visible()
    beat(page, 2000)
    shot(page, "07-learn")
    hero = page.get_by_role("heading", name="Same empty plate, different evidence")
    hero.scroll_into_view_if_needed()
    expect(page.get_by_text("Not used for learning.")).to_be_visible()
    beat(page, 2500)
    page.get_by_role("button", name="“That was enough”").click()
    expect(page.get_by_text("interval likelihood")).to_be_visible()
    beat(page, 2500)
    page.get_by_role("button", name="“I wanted more”").click()
    expect(page.get_by_text("right-censored likelihood")).to_be_visible()
    beat(page, 3000)
    page.locator("section", has=hero).screenshot(path=str(SHOTS / "08-learn-same-empty-plate.png"))
    print("  shot 08-learn-same-empty-plate")

    # ---- Kitchen: plan, then make it infeasible
    page.get_by_role("link", name="Kitchen").click()
    expect(page.get_by_role("heading", name="Preparation plan")).to_be_visible()
    expect(page.get_by_text("Feasible", exact=True)).to_be_visible()
    beat(page, 3000)
    shot(page, "09-kitchen-plan")
    total = page.locator(".kpi", has_text="Expected to be served").locator(".v")
    before = total.inner_text()
    page.get_by_role("spinbutton", name=re.compile("Expected diners")).fill("200")
    expect(total).not_to_have_text(before, timeout=15000)          # a real recomputation
    beat(page, 3000)
    page.get_by_role("spinbutton", name=re.compile("Stock on hand")).fill("25")
    expect(page.get_by_text("This plan cannot meet its targets as set")).to_be_visible(timeout=15000)
    expect(page.get_by_text("Not enough stock.")).to_be_visible()
    beat(page, 3000)
    shot(page, "10-kitchen-infeasible")

    # ---- Evidence
    page.get_by_role("link", name="Evidence").click()
    expect(page.get_by_text("Simulated · not measured savings").first).to_be_visible(timeout=15000)
    expect(page.get_by_text("Like for like.")).to_be_visible()
    beat(page, 4000)
    shot(page, "11-evidence-simulated")
    page.get_by_role("button", name="Only half walk back for seconds").click()
    beat(page, 2500)
    page.get_by_label("Scenario").select_option("seconds_friction")
    beat(page, 2500)
    shot(page, "12-evidence-seconds-friction")
    page.get_by_role("button", name="Predicted").click()
    expect(page.get_by_text("Did earlier plates predict the next one?").first).to_be_visible()
    beat(page, 2000)
    shot(page, "13-evidence-predicted")
    page.get_by_role("button", name="Observed").click()
    expect(page.get_by_text("No observed records in the demo")).to_be_visible()
    beat(page, 2000)
    shot(page, "14-evidence-observed-empty")
    with page.expect_download() as dl:
        page.get_by_role("button", name="Export plates (CSV)").click()
    head = Path(dl.value.path()).read_text(encoding="utf-8").splitlines()[0]
    assert head.startswith("service_date,session_id,diner_token"), head

    # ---- Setup, deep link reload, missing asset, reset
    page.goto(BASE + "/demo/setup")
    expect(page.get_by_role("heading", name="Foods and scoop calibration")).to_be_visible()
    shot(page, "15-setup")
    r = page.request.get(BASE + "/assets/does-not-exist.js")
    assert r.status in (403, 404), f"missing asset returned {r.status}"
    assert "<!doctype html" not in r.text().lower(), "a missing asset must not return the app shell"
    page.get_by_role("button", name=re.compile("Reset demo")).click()
    page.get_by_role("button", name="Yes, reset").click()
    page.get_by_role("link", name="Serve").click()
    page.get_by_role("button", name="T-104", exact=True).click()
    expect(page.get_by_text("Suggested first portion")).to_be_visible()   # the plate recorded earlier is gone
    real = [e for e in errors if "favicon" not in e and "does-not-exist" not in e and "404" not in e]
    assert not real, f"browser errors: {real[:5]}"


def phone(page: Page) -> None:
    page.goto(BASE + "/demo/serve")
    page.get_by_role("button", name="T-415", exact=True).click()
    expect(page.get_by_text("Suggested first portion")).to_be_visible()
    shot(page, "20-phone-serve")
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"page scrolls sideways on a phone by {overflow}px"
    page.keyboard.press("Tab")
    page.goto(BASE + "/demo/learn")
    expect(page.get_by_role("heading", name="Same empty plate, different evidence")).to_be_visible()
    shot(page, "21-phone-learn")
    page.goto(BASE + "/demo/kitchen")
    expect(page.get_by_role("heading", name="Preparation plan")).to_be_visible()
    shot(page, "22-phone-kitchen")
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport={"width": 1366, "height": 860}, accept_downloads=True,
                                  record_video_dir=str(ROOT / "build" / "video") if VIDEO else None,
                                  record_video_size={"width": 1366, "height": 860} if VIDEO else None)
        print("desktop walkthrough against", BASE)
        page = ctx.new_page()
        page.set_default_timeout(12000)
        desktop(page)
        ctx.close()
        if VIDEO:
            print("  video:", page.video.path())
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
        print("phone walkthrough")
        page = ctx.new_page()
        page.set_default_timeout(12000)
        phone(page)
        ctx.close()
        browser.close()
    print("WALKTHROUGH PASSED")


if __name__ == "__main__":
    main()
