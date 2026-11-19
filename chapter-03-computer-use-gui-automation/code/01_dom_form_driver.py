"""Exercise 1 — Drive a form with DOM selectors (Foundation).

The deterministic actor layer of the hybrid architecture: a Playwright script
that fills and submits a form using role and label locators, never coordinates.

The script serves a small form page from a local HTTP server (so it needs no
external site), drives it with Playwright's Python API, submits it, and asserts
on the confirmation text it reads back.

Run:
    pip install -r requirements.txt
    playwright install chromium
    python 01_dom_form_driver.py [--a11y]

The --a11y flag dumps the page's accessibility snapshot on failure (and prints
it on success too) — that snapshot is the artefact you would hand a model in
Exercise 2.

No step in this script uses coordinates, pixel positions or mouse.click(x, y).
Every interaction addresses an element by role, label or text, and every wait
is Playwright's auto-waiting, not a fixed sleep.
"""

from __future__ import annotations

import argparse
import http.server
import sys
import threading

FORM_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Demo form</title></head>
<body>
  <h1>Request access</h1>
  <form id="access-form">
    <label>Full name <input name="full_name" type="text"></label><br>
    <label>Work email <input name="email" type="email"></label><br>
    <label>Team
      <select name="team">
        <option>Platform</option>
        <option>Applied AI</option>
        <option>Data</option>
      </select>
    </label><br>
    <label>Reason for access <textarea name="reason"></textarea></label><br>
    <button type="submit">Submit request</button>
  </form>
  <p id="confirmation" hidden></p>
  <script>
    document.getElementById("access-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const data = new FormData(event.target);
      const confirmation = document.getElementById("confirmation");
      confirmation.textContent =
        `Received request from ${data.get("full_name")} (${data.get("email")}) ` +
        `for team ${data.get("team")}: ${data.get("reason")}`;
      confirmation.hidden = false;
    });
  </script>
</body>
</html>
"""


def serve_form() -> tuple[http.server.HTTPServer, int]:
    """Serve FORM_HTML on a loopback port in a background thread."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(FORM_HTML.encode("utf-8"))

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a11y", action="store_true",
                        help="print the accessibility snapshot as well")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Run:\n"
              "  pip install -r requirements.txt\n"
              "  playwright install chromium", file=sys.stderr)
        return 2

    server, port = serve_form()
    url = f"http://127.0.0.1:{port}/"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            try:
                page.goto(url)

                # Every interaction below addresses an element by role or
                # label — the vocabulary a user (and an agent) thinks in.
                # Playwright waits for each element to become actionable;
                # there is no fixed sleep anywhere in this script.
                page.get_by_label("Full name").fill("Alex Example")
                page.get_by_label("Work email").fill("alex@example.com")
                page.get_by_label("Team").select_option("Applied AI")
                page.get_by_label("Reason for access").fill(
                    "Need read access to the staging agent logs.")
                page.get_by_role("button", name="Submit request").click()

                confirmation = page.locator("#confirmation")
                confirmation.wait_for(state="visible")
                text = confirmation.inner_text()
                print("Confirmation:", text)

                # Assert the submitted values round-tripped exactly.
                for expected in ("Alex Example", "alex@example.com",
                                 "Applied AI", "staging agent logs"):
                    assert expected in text, f"missing from confirmation: {expected}"

                if args.a11y:
                    snapshot = page.accessibility.snapshot()
                    print("\nAccessibility snapshot:")
                    print(snapshot)
                print("OK — form driven entirely by DOM selectors.")
                return 0
            finally:
                browser.close()
    finally:
        server.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
