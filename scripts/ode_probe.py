"""Explore the ODE report-card Media page with Playwright to find the real
download links (the Kendo grid + AJAX need a browser session). Read-only probe:
loads the page, captures Media/Get* AJAX responses, dumps the year dropdown and
any data-file links. Run with house-hunter's venv (has playwright; chromium is in
%LOCALAPPDATA%\\ms-playwright). ASCII only.
"""
import json

from playwright.sync_api import sync_playwright

URL = "https://www.ode.state.or.us/data/ReportCard/Media"
captured = []


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page()

        def on_resp(r):
            if "Media/Get" in r.url:
                body = ""
                try:
                    body = r.text()
                except Exception:
                    pass
                captured.append((r.url.split("/Media/")[-1],
                                 (r.request.post_data or "")[:60], body[:160]))

        pg.on("response", on_resp)
        pg.goto(URL, wait_until="networkidle", timeout=60000)
        pg.wait_for_timeout(2500)

        # Kendo keeps hidden <select>s; dump their ids + first options.
        selects = pg.eval_on_selector_all(
            "select",
            "els=>els.map(e=>({id:e.id,name:e.name,opts:[...e.options].slice(0,3).map(o=>o.value+':'+o.text)}))",
        )
        print("SELECTS:", json.dumps(selects)[:700])

        # Try selecting the 2023-2024 year via the Kendo API, then re-capture.
        try:
            pg.evaluate("""() => {
                const w = window.jQuery ? jQuery('select').filter((i,e)=>jQuery(e).data('kendoDropDownList')) : null;
            }""")
        except Exception as e:
            print("kendo select err:", e)
        pg.wait_for_timeout(1500)

        links = pg.eval_on_selector_all(
            "a",
            "els=>els.map(a=>a.href).filter(h=>/\\.(csv|xlsx|xls|zip)(\\?|$)/i.test(h)||/DocLink|Download|getmedia/i.test(h)).slice(0,25)",
        )
        print("FILE LINKS:", json.dumps(links)[:1000])
        b.close()

    print("CAPTURED Media/* calls:")
    for ep, pd, bd in captured:
        print(f"  {ep:<22} post={pd!r} body={bd!r}")


if __name__ == "__main__":
    main()
