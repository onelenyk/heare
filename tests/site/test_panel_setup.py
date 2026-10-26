"""Playwright proof for site/panel/setup.html with a fake Web Bluetooth device.

Run with a system Python that has Playwright and Chromium installed:

    python3 tests/site/test_panel_setup.py

The fake GATT server exercises connect, auth (bad then good), scan, save,
the post-save reconnect, and status. The run writes
.sisyphus/evidence/task-21-page.png and task-21-page.log.
"""

from __future__ import annotations

import functools
import http.server
import pathlib
import sys
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright.sync_playwright
expect = playwright.expect

ROOT = pathlib.Path(__file__).resolve().parents[2]
SITE = ROOT / "site"
EVIDENCE = ROOT / ".sisyphus" / "evidence"
SCREENSHOT = EVIDENCE / "task-21-page.png"
LOG = EVIDENCE / "task-21-page.log"

FAKE_JS = r"""
(() => {
  const SERVICE = "8f2a0001-7d3b-4f6e-9a1c-2b5e7c9d0a11";
  const UUID = {
    status: "8f2a0002-7d3b-4f6e-9a1c-2b5e7c9d0a11",
    scan: "8f2a0003-7d3b-4f6e-9a1c-2b5e7c9d0a11",
    cmd: "8f2a0004-7d3b-4f6e-9a1c-2b5e7c9d0a11",
    result: "8f2a0005-7d3b-4f6e-9a1c-2b5e7c9d0a11",
  };
  const encoder = new TextEncoder();
  const decoder = new TextDecoder();
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const bytesOf = (value) => encoder.encode(JSON.stringify(value));
  const viewOf = (value) => {
    const bytes = bytesOf(value);
    return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  };

  const scanList = () => [
    { ssid: "Studio-5G", rssi: -57 },
    { ssid: "HomeNet", rssi: -41 },
    { ssid: "Cafe Guest", rssi: -72 },
  ];

  const fake = {
    log: [],
    saved: null,
    authed: false,
    connectAttempts: 0,
    failNextConnect: false,
    requested: null,
    scan: { seq: 1, list: scanList() },
    status: { v: 1, state: "setup", name: "", ip: "", ssid: "", pid: "2364", error: "" },
    result: { id: 0, ok: false, error: "idle" },
    chunks: new Map(),
  };
  window.__fake = fake;

  function concat(parts) {
    const total = parts.reduce((sum, part) => sum + part.length, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    for (const part of parts) {
      out.set(part, offset);
      offset += part.length;
    }
    return out;
  }

  async function handle(id, message) {
    const entry = { id, cmd: message.cmd };
    fake.log.push(entry);
    if (message.cmd !== "auth" && !fake.authed) {
      entry.denied = true;
      fake.result = { id, ok: false, error: "auth required" };
      return;
    }
    if (message.cmd === "auth") {
      if (message.code === "1234") {
        fake.authed = true;
        entry.ok = true;
        fake.result = { id, ok: true, error: "" };
      } else {
        entry.ok = false;
        fake.result = { id, ok: false, error: "wrong code" };
      }
      return;
    }
    if (message.cmd === "scan") {
      fake.scan = { seq: fake.scan.seq + 1, list: scanList() };
      fake.result = { id, ok: true, error: "" };
      return;
    }
    if (message.cmd === "save") {
      fake.saved = JSON.parse(JSON.stringify(message));
      fake.status = {
        v: 1,
        state: "idle",
        name: message.name,
        ip: "192.168.110.77",
        ssid: message.ssid,
        pid: "2364",
        error: "",
      };
      fake.result = { id, ok: true, error: "" };
      entry.ok = true;
      setTimeout(() => {
        fake.failNextConnect = true;
        gatt.disconnect();
      }, 400);
      return;
    }
    if (message.cmd === "forget") {
      fake.saved = null;
      fake.status = { v: 1, state: "setup", name: "", ip: "", ssid: "", pid: "2364", error: "" };
      fake.result = { id, ok: true, error: "" };
      entry.ok = true;
      return;
    }
    fake.result = { id, ok: false, error: "unknown command" };
  }

  class Characteristic {
    constructor(uuid) {
      this.uuid = uuid;
    }

    async readValue() {
      await sleep(8);
      if (!gatt.connected) throw new DOMException("disconnected", "NetworkError");
      if (this.uuid === UUID.status) return viewOf(fake.status);
      if (this.uuid === UUID.scan) return viewOf(fake.scan);
      if (this.uuid === UUID.result) return viewOf(fake.result);
      throw new DOMException("not readable", "NotSupportedError");
    }

    async writeValue(value) {
      return this.writeValueWithResponse(value);
    }

    async writeValueWithResponse(value) {
      await sleep(8);
      if (!gatt.connected) throw new DOMException("disconnected", "NetworkError");
      if (this.uuid !== UUID.cmd) throw new DOMException("not writable", "NotSupportedError");
      const data = new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
      const seq = data[0];
      const total = data[1];
      const payload = data.slice(2);
      if (seq === 0) {
        fake.chunks = { total, parts: [] };
      }
      const entry = fake.chunks;
      if (!entry || entry.total !== total || seq !== entry.parts.length) {
        fake.chunks = null;
        fake.result = { id: null, ok: false, error: "chunk" };
        return;
      }
      entry.parts.push(payload);
      if (entry.parts.length < total) return;
      fake.chunks = null;
      let message;
      try {
        message = JSON.parse(decoder.decode(concat(entry.parts)));
      } catch (error) {
        fake.result = { id: null, ok: false, error: "bad json" };
        return;
      }
      await handle(message.id, message);
    }
  }

  const characteristics = {
    status: new Characteristic(UUID.status),
    scan: new Characteristic(UUID.scan),
    cmd: new Characteristic(UUID.cmd),
    result: new Characteristic(UUID.result),
  };

  const service = {
    uuid: SERVICE,
    async getCharacteristic(uuid) {
      await sleep(5);
      const key = Object.keys(UUID).find((name) => UUID[name] === String(uuid).toLowerCase());
      if (!key) throw new DOMException("no characteristic", "NotFoundError");
      return characteristics[key];
    },
    async getCharacteristics() {
      return Object.values(characteristics);
    },
  };

  let device;
  const gatt = {
    connected: false,
    async connect() {
      fake.connectAttempts += 1;
      await sleep(30);
      if (fake.failNextConnect) {
        fake.failNextConnect = false;
        throw new DOMException("panel rebooting", "NetworkError");
      }
      gatt.connected = true;
      return gatt;
    },
    disconnect() {
      if (!gatt.connected) return;
      gatt.connected = false;
      fake.authed = false;
      device.dispatchEvent(new Event("gattserverdisconnected"));
    },
    async getPrimaryService(uuid) {
      await sleep(5);
      if (!gatt.connected) throw new DOMException("disconnected", "NetworkError");
      if (String(uuid).toLowerCase() !== SERVICE) throw new DOMException("no service", "NotFoundError");
      return service;
    },
  };

  device = new EventTarget();
  device.id = "fake-panel-2364";
  device.name = "heare-panel-2364";
  device.gatt = gatt;

  Object.defineProperty(navigator, "bluetooth", {
    configurable: true,
    value: {
      async requestDevice(options) {
        fake.requested = JSON.parse(JSON.stringify(options));
        return device;
      },
    },
  });
})();
"""


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass


def start_server() -> http.server.ThreadingHTTPServer:
    handler = functools.partial(QuietHandler, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def run() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    server = start_server()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    page_url = base + "/panel/setup.html"
    lines: list[str] = []
    failures: list[str] = []

    def note(text: str) -> None:
        lines.append(text)
        print(text)

    def check(condition: bool, text: str) -> None:
        if condition:
            note("PASS " + text)
        else:
            failures.append(text)
            note("FAIL " + text)

    try:
        with sync_playwright() as playwright_driver:
            browser = playwright_driver.chromium.launch()

            unsupported = browser.new_context(viewport={"width": 1100, "height": 900})
            unsupported.add_init_script(
                'Object.defineProperty(navigator, "bluetooth", { configurable: true, value: undefined });'
            )
            page = unsupported.new_page()
            page.goto(page_url)
            check(
                page.locator("#support").is_visible(), "unsupported notice is visible"
            )
            check(
                "Web Bluetooth" in page.locator("#support").inner_text(),
                "unsupported notice names Web Bluetooth",
            )
            check(
                page.locator("#connect").is_disabled(),
                "connect is disabled without Web Bluetooth",
            )
            unsupported.close()

            context = browser.new_context(viewport={"width": 1100, "height": 1600})
            context.add_init_script(FAKE_JS)
            page = context.new_page()
            console: list[str] = []
            errors: list[str] = []
            requests: list[str] = []
            page.on(
                "console",
                lambda message: (
                    console.append(f"{message.type}: {message.text}"),
                    errors.append(message.text) if message.type == "error" else None,
                ),
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url))

            page.goto(page_url)
            expect(page.locator("#connect")).to_be_enabled()

            page.locator("#connect").click()
            expect(page.locator("#conn-text")).to_have_text("підключено", timeout=10000)
            check(
                page.evaluate("window.__fake.requested.filters[0].services[0]")
                == "8f2a0001-7d3b-4f6e-9a1c-2b5e7c9d0a11",
                "requestDevice filters by the panel service UUID",
            )

            page.locator("#setup-code").fill("0000")
            page.locator("#unlock").click()
            expect(page.locator("#message")).to_contain_text(
                "Код не прийнято", timeout=10000
            )
            check(True, "bad OLED code shows a visible error")

            page.locator("#setup-code").fill("1234")
            page.locator("#unlock").click()
            expect(page.locator("#conn-text")).to_have_text(
                "код прийнято", timeout=10000
            )
            expect(page.locator("#status-state")).to_have_text("setup", timeout=10000)
            check(True, "auth 1234 unlocks and reads status")

            page.locator("#scan").click()
            expect(page.locator("#message")).to_contain_text(
                "Знайдено мереж: 3", timeout=25000
            )
            page.locator("#ssid-select").select_option("HomeNet")
            check(
                page.locator("#ssid-select option").count() == 4,
                "scan filled the SSID select with three networks",
            )

            page.locator("#password").fill("correct-horse")
            page.locator("#brain-host").fill("192.168.110.77")
            page.locator("#brain-port").fill("9790")
            page.locator("#panel-name").fill("kitchen")
            page.locator("#pair-code").fill("abcd2345")
            check(
                page.locator("#pair-code").input_value() == "ABCD2345",
                "pair code is uppercased in the field",
            )

            page.locator("#save").click()
            expect(page.locator("#message")).to_contain_text(
                "Налаштування збережено", timeout=30000
            )
            expect(page.locator("#status-ip")).to_have_text(
                "192.168.110.77", timeout=20000
            )
            expect(page.locator("#conn-text")).to_have_text("збережено", timeout=20000)
            check(True, "save is confirmed by result and the panel reconnects")

            saved = page.evaluate("window.__fake.saved")
            check(isinstance(saved.get("id"), int), "save payload carries a command id")
            saved.pop("id", None)
            expected = {
                "cmd": "save",
                "ssid": "HomeNet",
                "pass": "correct-horse",
                "brain_host": "192.168.110.77",
                "brain_port": 9790,
                "name": "kitchen",
                "pair_code": "ABCD2345",
            }
            check(saved == expected, "save payload matches the BLE contract exactly")
            check(
                all(
                    key not in saved for key in ("token", "master", "part_token", "ap")
                ),
                "save payload carries no brain token or AP field",
            )

            commands = page.evaluate("window.__fake.log")
            order = [entry["cmd"] for entry in commands]
            check(
                order.index("auth") < order.index("scan") < order.index("save"),
                "auth precedes scan and save",
            )
            auth_entries = [entry for entry in commands if entry["cmd"] == "auth"]
            check(
                len(auth_entries) >= 2
                and auth_entries[0]["ok"] is False
                and auth_entries[1]["ok"] is True,
                "bad auth is rejected before the good auth",
            )
            check(
                page.evaluate("window.__fake.connectAttempts") >= 2,
                "gatt.connect() was retried after the simulated reboot",
            )

            storage = page.evaluate(
                "({ local: Object.fromEntries(Object.entries(localStorage)),"
                " session: Object.fromEntries(Object.entries(sessionStorage)) })"
            )
            check(
                storage == {"local": {}, "session": {}},
                "nothing is written to browser storage",
            )
            url = page.url
            check(
                all(
                    secret not in url
                    for secret in ("correct-horse", "ABCD2345", "1234")
                ),
                "URL carries no code, password, or pair code",
            )

            external = [
                url
                for url in requests
                if not url.startswith(base) and not url.startswith("data:")
            ]
            check(not external, "the page makes no external requests")
            check(not errors, "browser console and page errors are clean")

            page.screenshot(path=str(SCREENSHOT), full_page=True)
            note(f"screenshot={SCREENSHOT.relative_to(ROOT)}")
            note(f"url={page_url}")
            for line in console:
                note("console " + line)
            context.close()

            file_context = browser.new_context(viewport={"width": 1100, "height": 900})
            file_context.add_init_script(FAKE_JS)
            file_page = file_context.new_page()
            file_page.goto((SITE / "panel" / "setup.html").as_uri())
            expect(file_page.locator("#connect")).to_be_enabled(timeout=5000)
            check(
                file_page.title().startswith("heare"),
                "the page also opens from a file:// URL",
            )
            file_context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()

    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if failures:
        note(f"RESULT FAIL ({len(failures)} failed)")
        for failure in failures:
            note("  " + failure)
        return 1
    note("RESULT PASS")
    return 0


def test_panel_setup_flow() -> None:
    assert run() == 0


if __name__ == "__main__":
    sys.exit(run())
