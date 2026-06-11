"""Screenshot the live comparison UI (Chart view) for visual verification."""
import os
import pathlib

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8011"
OUT = str(pathlib.Path(os.environ["LOCALAPPDATA"]) / "school-lens" / "cmp_chart.png")
OUT2 = str(pathlib.Path(os.environ["LOCALAPPDATA"]) / "school-lens" / "cmp_table.png")


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.goto(URL, wait_until="networkidle", timeout=60000)
        pg.fill("#addr", "12345 SW Allen Blvd, Beaverton")
        pg.click("#compare")
        pg.wait_for_selector("table.cmp", timeout=30000)
        pg.wait_for_timeout(600)
        pg.screenshot(path=OUT2)
        pg.click("button.vbtn:has-text('Chart')")
        pg.wait_for_selector(".barrow", timeout=15000)
        pg.wait_for_timeout(600)
        pg.screenshot(path=OUT)
        b.close()
    print("table:", OUT2)
    print("chart:", OUT)


if __name__ == "__main__":
    main()
