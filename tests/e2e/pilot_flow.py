"""Browser flow through the authenticated pilot workspace.

    python tests/e2e/pilot_flow.py http://127.0.0.1:8000 --user manager          # local dev sign-in
    set PP_PASSWORD=...  &&  python tests/e2e/pilot_flow.py https://<app> --user you@example.org

Calibrate -> create service -> register token -> recommend -> serve -> seconds ->
return -> changed recommendation -> persisted across reload -> observed evidence -> sign out.
The password is read from the PP_PASSWORD environment variable, never from the command line.
Each run creates its own food, service and token, so it can be repeated.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "screenshots"
BASE = sys.argv[1].rstrip("/")
USER = sys.argv[sys.argv.index("--user") + 1]
PASSWORD = os.environ.get("PP_PASSWORD", "")
RUN = time.strftime("%H%M%S")


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        page = browser.new_context(viewport={"width": 1366, "height": 860}).new_page()
        page.set_default_timeout(15000)

        # signed out: the pilot workspace sends you to sign-in
        page.goto(BASE + "/pilot/serve")
        expect(page.get_by_role("heading", name="Pilot workspace sign-in")).to_be_visible()
        page.locator("input").first.fill(USER)
        if PASSWORD:
            page.get_by_label("Password").fill(PASSWORD)
        page.get_by_role("button", name="Sign in").click()
        expect(page.get_by_text(re.compile(r"Pilot · "))).to_be_visible()

        # setup: food, calibration, service, token
        page.goto(BASE + "/pilot/setup")
        food = f"Rice {RUN}"
        page.get_by_label("Food name").fill(food)
        page.get_by_role("button", name="Add food").click()
        expect(page.get_by_role("cell", name=food)).to_be_visible()
        expect(page.get_by_text("not calibrated").first).to_be_visible()
        page.locator("form", has_text="Record a scoop calibration").locator("select").first.select_option(label=f"{food} · v1")
        page.get_by_label("Scoop weights in grams").fill("58 61 60 63 59 62 60 57 61 60 59 62 61 58 60 63 60 59 61 60")
        expect(page.get_by_text("20 scoops · mean 60.2 g")).to_be_visible()   # 1204 g / 20, computed in the browser
        page.get_by_role("button", name="Save calibration").click()
        expect(page.get_by_text("Calibration saved as a new version")).to_be_visible()
        page.get_by_label("Name", exact=True).fill(f"Lunch {RUN}")
        page.get_by_role("button", name="Create service").click()
        expect(page.get_by_text("Meal service created")).to_be_visible()
        token = f"T-{RUN}"
        page.get_by_label("Token").fill(token)
        page.get_by_label("Diner agreed to take part (18+)").check()
        page.get_by_role("button", name="Register token").click()
        expect(page.get_by_role("cell", name=token)).to_be_visible()
        page.screenshot(path=str(SHOTS / "30-pilot-setup.png"), full_page=True)

        # serve (pick this run's service first: the food list follows the service's menu)
        page.get_by_role("link", name="Serve").click()
        service = page.locator("select").first.locator("option", has_text=f"Lunch {RUN}").inner_text()
        page.get_by_label("Meal service").select_option(label=service)
        page.get_by_label("Food").select_option(label=f"{food} · recipe v1")
        page.get_by_label("Diner token").fill(token)
        page.get_by_role("button", name="Find").click()
        expect(page.get_by_text("Measured history")).to_be_visible()
        expect(page.get_by_text("No history yet. This is the kitchen's usual first serving.")).to_be_visible()
        page.get_by_role("button", name=re.compile(r"^Serve 3 scoops")).click()
        page.get_by_role("button", name="+ ½ scoop").click()
        expect(page.get_by_text(re.compile(r"seconds 30 g"))).to_be_visible()
        page.get_by_label("Edible food left in grams").fill("70")
        page.get_by_role("button", name="Enough", exact=True).click()
        page.get_by_role("button", name="Had enough").click()
        page.get_by_role("button", name="Save return").click()
        expect(page.get_by_text("Saved to the pilot record")).to_be_visible()
        expect(page.get_by_text("This plate updated the estimate.")).to_be_visible()
        after = page.locator(".beforeafter > div").nth(2).inner_text()
        assert "3 scoops" not in after, f"suggestion did not change after a real observation: {after!r}"
        page.screenshot(path=str(SHOTS / "31-pilot-serve.png"), full_page=True)

        # durable: a reload reads the plate back from the server
        page.reload()
        try:
            expect(page.get_by_text(re.compile(r"Pilot · "))).to_be_visible()
        except AssertionError:
            page.screenshot(path=str(ROOT / "build" / "pilot-fail.png"))
            print("after reload url:", page.url, "| body:", page.locator("body").inner_text()[:300])
            raise
        page.get_by_label("Meal service").select_option(label=service)
        page.get_by_label("Food").select_option(label=f"{food} · recipe v1")
        page.get_by_label("Diner token").fill(token)
        page.get_by_role("button", name="Find").click()
        expect(page.get_by_text("Came back")).to_be_visible()
        expect(page.get_by_text("70 g").first).to_be_visible()

        # correction is audited and recomputes
        page.get_by_role("button", name="Correct this return").click()
        page.get_by_label("Edible food left in grams").fill("20")
        page.get_by_role("button", name="Save correction").click()
        expect(page.get_by_text("Saved to the pilot record")).to_be_visible()
        expect(page.locator(".kpi", has_text="Came back").locator(".v")).to_have_text("20 g")

        # evidence: observed, measured
        page.get_by_role("link", name="Evidence").click()
        expect(page.get_by_text("Observed · declared by operators").first).to_be_visible()
        expect(page.get_by_text("Recorded plates").first).to_be_visible()
        page.screenshot(path=str(SHOTS / "32-pilot-evidence.png"), full_page=True)

        # sign out; the workspace is closed again
        page.get_by_role("button", name="Sign out").click()
        page.goto(BASE + "/pilot/evidence")
        expect(page.get_by_role("heading", name="Pilot workspace sign-in")).to_be_visible()
        browser.close()
    print("PILOT FLOW PASSED", food, token)


if __name__ == "__main__":
    main()
