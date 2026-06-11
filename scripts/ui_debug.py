"""Headless inspection of the map after findAssigned: layer counts + console errors."""
import json
import os
import pathlib

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8011"
OUT = str(pathlib.Path(os.environ["LOCALAPPDATA"]) / "school-lens" / "ui_debug.png")
logs = []

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    pg.on("console", lambda m: logs.append(f"{m.type}: {m.text}"))
    pg.on("pageerror", lambda e: logs.append(f"PAGEERROR: {e}"))
    pg.goto(URL, wait_until="networkidle", timeout=60000)
    pg.evaluate("""async () => {
        document.getElementById('addr').value = '12345 SW ALLEN BLVD';
        await findAssigned();
    }""")
    pg.wait_for_timeout(2500)
    counts = pg.evaluate("""() => {
        let markers = 0, lines = 0, polys = 0, tiles = 0, other = 0;
        function cls(l) {
            if (l instanceof L.CircleMarker) markers++;
            else if (l instanceof L.Polygon) polys++;
            else if (l instanceof L.Polyline) lines++;
            else if (l instanceof L.TileLayer) tiles++;
            else if (l.eachLayer) l.eachLayer(cls);
            else other++;
        }
        map.eachLayer(cls);
        return { markers, lines, polys, tiles, other };
    }""")
    print("LAYER COUNTS:", json.dumps(counts))
    pg.screenshot(path=OUT)
    b.close()

print("CONSOLE / ERRORS (last 20):")
for line in logs[-20:]:
    print("  ", line)
print("shot:", OUT)
