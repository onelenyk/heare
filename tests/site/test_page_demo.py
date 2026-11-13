"""The chat-first page, looked at: the demo at 1440 and 390.

Run with a Python that has Playwright and Chromium:

    python3 tests/site/test_page_demo.py

It serves the page, its CSS and its script itself, injects the Ukrainian
catalog the way the brain does, and writes two screenshots under
.sisyphus/evidence/. It is not part of make test: it needs a browser.
"""

from __future__ import annotations

import http.server
import json
import pathlib
import sys
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
API = ROOT / "packages/heare-brain/heare/api"
PAGE = API / "page.html"
CSS = API / "page.css"
JS = API / "page.js"
EVIDENCE = ROOT / ".sisyphus" / "evidence"

for package in ("heare-core", "heare-brain"):
    sys.path.insert(0, str(ROOT / "packages" / package))

WORDS_JS = """
const I18N = %s;
const fill = (s, vars) => vars ? s.replace(/\\{(\\w+)\\}/g, (m, k) => k in vars ? vars[k] : m) : s;
const tr = (key, vars) => fill(typeof I18N[key] === "string" ? I18N[key] : key, vars);
const trn = (n, one, other, vars) => {
  const forms = I18N[one], d = n %% 10, h = n %% 100;
  const i = !Array.isArray(forms) ? (n === 1 ? 0 : 1)
    : document.documentElement.lang === "uk" ? (d === 1 && h !== 11 ? 0 : d >= 2 && d <= 4 && (h < 12 || h > 14) ? 1 : 2)
    : (n === 1 ? 0 : 1);
  const s = Array.isArray(forms) ? forms[Math.min(i, forms.length - 1)] : [one, other][i];
  return fill(s, {n, ...vars});
};
document.querySelectorAll("[data-t]").forEach(e => { e.textContent = tr(e.textContent.trim()); });
for (const [mark, attr] of [["title", "title"], ["placeholder", "placeholder"], ["aria", "aria-label"]])
  document.querySelectorAll(`[data-t-${mark}]`).forEach(e => e.setAttribute(attr, tr(e.getAttribute(attr))));
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body = PAGE.read_text(encoding="utf-8")
            from heare.words import catalog
            words = json.dumps(catalog("uk"), ensure_ascii=False)
            body = body.replace("/*WORDS*/", WORDS_JS % words)
            self._send(body.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/page.css":
            self._send(CSS.read_bytes(), "text/css; charset=utf-8")
        elif path == "/page.js":
            self._send(JS.read_bytes(), "text/javascript; charset=utf-8")
        else:
            self.send_error(404)

    def _send(self, body: bytes, kind: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def run() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    failures = []

    def check(ok, what):
        print(("PASS " if ok else "FAIL ") + what)
        if not ok:
            failures.append(what)

    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch()
            for name, width, height in (("1440", 1440, 900), ("390", 390, 844)):
                page = browser.new_page(viewport={"width": width, "height": height})
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "/?demo=1")
                page.wait_for_timeout(1200)
                check(page.locator("#chat").is_visible(), f"{name}: the chat is there")
                check(page.locator("#flow .msg").count() == 15, f"{name}: fifteen messages")
                check(page.locator(".pinchip").count() == 4, f"{name}: four pins")
                check(page.locator("#hold").is_hidden(), f"{name}: nothing waits")
                check(not errors, f"{name}: no page errors")
                page.screenshot(path=str(EVIDENCE / f"page-demo-{name}.png"))
                # the chat selector: the name in the bar, the list in a menu
                check(page.locator("#chatbtn").is_visible(), f"{name}: the chat chip is in the bar")
                page.click("#chatbtn")
                page.wait_for_timeout(250)
                check(page.locator("#chatmenu").is_visible(), f"{name}: the chat menu opens")
                check(page.locator("#chatrows .chatrow").count() == 3, f"{name}: three chats")
                check(page.locator("#chatrows .chatrow.current").count() == 1, f"{name}: one current chat")
                page.screenshot(path=str(EVIDENCE / f"page-demo-menu-{name}.png"))
                page.keyboard.press("Escape")
                page.wait_for_timeout(150)
                check(page.locator("#chatmenu").is_hidden(), f"{name}: Escape closes the chat menu")
                page.close()
            browser.close()
    finally:
        server.shutdown()
    print("screenshots: .sisyphus/evidence/page-demo-1440.png, page-demo-390.png, page-demo-menu-1440.png, page-demo-menu-390.png")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(run())
