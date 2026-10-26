"""The boot orchestrator, driven with fakes for every hardware boundary.

``main`` imports no ``machine``/``framebuf`` at module level, so these tests
can import it on a laptop. The display is a recording panel, the radio a
canned station, the link a scripted thread body, and the watchdog counts its
feeds; nothing here sleeps or touches USB.
"""

from __future__ import annotations

import pathlib

import main
from heare_panel import link as link_module
from heare_panel import net, render, state
from heare_panel.store import Store
from heare_panel.viewport import Viewport

from tests.firmware.fakes import FakeCanvas, FakeNVS

TICKS_PERIOD = 1 << 30


def _config(name="esp32c3-abcd"):
    return {
        "version": 1,
        "wifi": {"ssid": "home", "password": "secret123"},
        "brain": {"host": "192.168.1.10", "port": 9790},
        "device": {"name": name},
    }


def _store(tmp_path):
    return Store(FakeNVS(), path=str(tmp_path / "config.json"))


def _provisioned(tmp_path, boots=0):
    store = _store(tmp_path)
    store.save_config(_config())
    store.set_part_token("part-token-not-a-secret-for-this-test")
    store.set_boot_count(boots)
    return store


class Clock:
    """A callable ``now_ms`` source; ``heare_panel.clock`` is a module."""

    def __init__(self):
        self.ms = 0

    def now_ms(self):
        return self.ms

    def ticks_ms(self):
        return self.ms

    def advance(self, ms):
        self.ms = (self.ms + ms) % TICKS_PERIOD

    @staticmethod
    def elapsed(start, now):
        return (now - start + TICKS_PERIOD // 2) % TICKS_PERIOD - TICKS_PERIOD // 2


class Panel:
    def __init__(self, available=True):
        self.available = available
        self.width = 128
        self.height = 64
        self.view_width = 72
        self.view_height = 40
        self.xoff = 28
        self.yoff = 24
        self.frames = []
        self.viewports = []

    def viewport(self, canvas):
        self.viewports.append(canvas)
        return Viewport(canvas, self.xoff, self.yoff, self.view_width, self.view_height)

    def flush(self, frame):
        self.frames.append(bytes(frame))
        return True


class Buffer:
    def __init__(self, data, width, height, mode):
        self.data = data
        self.width = width
        self.height = height
        self.canvas = FakeCanvas(width, height)

    def __getattr__(self, name):
        return getattr(self.canvas, name)


class Framebuf:
    MONO_VLSB = 0
    FrameBuffer = Buffer


class Wdt:
    def __init__(self):
        self.feeds = 0

    def feed(self):
        self.feeds += 1


class Sta:
    def __init__(self, ip="192.168.1.50", error=None, networks=()):
        self.ip_value = ip
        self.error = error
        self.networks = list(networks)
        self.connects = []
        self.scans = 0

    def connect(self, ssid, password):
        self.connects.append((ssid, password))
        if self.error is not None:
            raise self.error
        return self.ip_value

    def scan(self):
        self.scans += 1
        return list(self.networks)


class Link:
    def __init__(self, config, store, clock=None, on_state=None, fail=None):
        self.config = config
        self.store = store
        self.display = state.initial("brain")
        self.fail = fail
        self.reports = []
        self.closed = False
        self.supervised = 0

    def supervise(self):
        self.supervised += 1
        if self.fail is not None:
            raise self.fail
        self.display = state.reduce(self.display, {}, 0, "online")

    def report(self, values):
        self.reports.append(dict(values))
        return True

    def close(self):
        self.closed = True


class LinkFactory:
    def __init__(self, fail=None):
        self.fail = fail
        self.links = []

    def __call__(self, config, store, clock=None, on_state=None):
        link = Link(config, store, clock=clock, on_state=on_state, fail=self.fail)
        self.links.append(link)
        return link


class Provisioner:
    def __init__(self, result=None):
        self.commands = []
        self.result = result or {"ok": True, "error": ""}
        self.ip = ""
        self.error = ""

    def handle(self, command):
        self.commands.append(dict(command))
        return self.result


class BleFactory:
    def __init__(self, on_poll=None):
        self.on_poll = on_poll
        self.setup = None
        self.calls = []

    def __call__(
        self,
        name,
        handler,
        code,
        window_ms=main.BLE_WINDOW_MS,
        status_provider=None,
        scan_provider=None,
        out=print,
        one_connection=True,
    ):
        self.calls.append(
            {
                "name": name,
                "code": code,
                "window_ms": window_ms,
                "handler": handler,
                "status": status_provider,
                "scan": scan_provider,
                "out": out,
            }
        )
        self.setup = Ble(
            self.calls[-1], self.on_poll
        )
        return self.setup


class Ble:
    def __init__(self, spec, on_poll=None):
        self.spec = spec
        self.name = spec["name"]
        self.handler = spec["handler"]
        self.code = spec["code"]
        self.window_ms = spec["window_ms"]
        self.status_provider = spec["status"]
        self.scan_provider = spec["scan"]
        self.on_poll = on_poll
        self.running = False
        self.starts = 0
        self.stops = 0
        self.polls = 0

    def start(self):
        self.running = True
        self.starts += 1
        return True

    def poll(self):
        self.polls += 1
        if self.on_poll is not None:
            self.on_poll(self)
        return self.running

    def stop(self):
        self.running = False
        self.stops += 1
        return True


def _record_screens(monkeypatch):
    screens = []

    def show(panel, word, now_ms=0, framebuf_module=None):
        screens.append(word)
        return True

    def boot(panel, framebuf_module=None):
        screens.append("BOOT")
        return True

    monkeypatch.setattr(main, "show_state", show)
    monkeypatch.setattr(main, "show_setup", lambda *args, **kwargs: True)
    monkeypatch.setattr(main, "show_boot", boot)
    return screens


def test_setup_is_needed_without_a_config(tmp_path):
    assert main.setup_required(_store(tmp_path)) is True


def test_wifi_without_a_part_token_is_not_a_bluetooth_boot(tmp_path):
    store = _store(tmp_path)
    store.save_config(_config())
    assert main.setup_required(store) is False


def test_setup_is_needed_after_three_consecutive_boots(tmp_path):
    store = _provisioned(tmp_path, boots=3)
    assert main.setup_required(store) is True


def test_two_healthy_boots_do_not_force_setup(tmp_path):
    store = _provisioned(tmp_path, boots=2)
    assert main.setup_required(store) is False


def test_record_boot_increments_the_counter(tmp_path):
    store = _store(tmp_path)
    main.record_boot(store)
    main.record_boot(store)
    assert store.boot_count() == 2


def test_show_state_renders_through_the_72x40_viewport():
    panel = Panel()

    assert main.show_state(panel, render.WIFI, 0, Framebuf()) is True

    buffer = panel.viewports[0]
    ops = buffer.canvas.ops
    word = [op for op in ops if op[0] == "text" and op[1] == "WIFI"]
    assert word == [("text", "WIFI", 28 + 20, 24, 1)]
    assert len(panel.frames[0]) == panel.width * panel.height // 8


def test_show_setup_puts_the_code_inside_the_window():
    panel = Panel()

    assert main.show_setup(panel, "1234", Framebuf()) is True

    ops = panel.viewports[0].canvas.ops
    assert ("text", "SETUP", 28 + 16, 24 + 4, 1) in ops
    assert ("text", "1234", 28 + 20, 24 + 20, 1) in ops


def test_a_missing_display_does_not_raise():
    panel = Panel(available=False)
    assert main.show_state(panel, render.BOOT, 0, Framebuf()) is False
    assert main.show_setup(panel, "1234", Framebuf()) is False


def test_normal_boot_goes_boot_wifi_brain_idle(tmp_path, monkeypatch):
    screens = _record_screens(monkeypatch)
    logs = []
    wdt = Wdt()

    result = main.run(
        store=_provisioned(tmp_path),
        sta=Sta(),
        panel=Panel(),
        wdt=wdt,
        reset=lambda: None,
        thread_start=lambda target, args: target(*args),
        link_factory=LinkFactory(),
        clock=Clock(),
        out=logs.append,
        sleep=lambda ms: None,
        ticks_normal=3,
    )

    assert result == "stopped"
    assert screens == ["BOOT", "WIFI", "BRAIN", "IDLE"]
    assert logs.count("BOOT") == 1
    assert wdt.feeds >= 1


def test_ble_handler_status_scan_and_name_are_wired(tmp_path, monkeypatch):
    _record_screens(monkeypatch)
    factory = BleFactory()
    provisioner = Provisioner()
    sta = Sta(networks=[{"ssid": "home", "rssi": -42}])

    main.run_setup(
        _store(tmp_path),
        sta,
        Panel(),
        tag="2364",
        code="1234",
        ble_factory=factory,
        provisioner=provisioner,
        wdt=Wdt(),
        reset=lambda: None,
        clock=Clock(),
        out=lambda line: None,
        sleep=lambda ms: None,
        max_ticks=2,
    )

    call = factory.calls[0]
    assert call["name"] == "heare-panel-2364"
    assert call["code"] == "1234"
    assert call["window_ms"] == main.BLE_WINDOW_MS
    assert call["handler"] == provisioner.handle

    call["handler"]({"cmd": "save", "ssid": "home"})
    call["status"]()
    call["scan"]()
    assert provisioner.commands[0]["cmd"] == "save"
    assert provisioner.commands[-1]["cmd"] == "status"
    assert sta.scans == 1


def test_ble_save_success_shows_idle_and_reboots(tmp_path, monkeypatch):
    screens = _record_screens(monkeypatch)
    reset = []
    store = _store(tmp_path)
    store.set_boot_count(3)

    def save_on_poll(setup):
        setup.handler(
            {
                "cmd": "save",
                "ssid": "home",
                "password": "secret123",
                "brain_host": "192.168.1.10",
                "brain_port": "9790",
                "name": "esp32-c3-abcd",
                "pair_code": "ABCD2345",
            }
        )
        setup.running = False

    factory = BleFactory(on_poll=save_on_poll)
    provisioner = Provisioner({"ok": True, "error": ""})
    provisioner.ip = "192.168.1.50"

    result = main.run_setup(
        store,
        Sta(),
        Panel(),
        tag="2364",
        code="1234",
        ble_factory=factory,
        provisioner=provisioner,
        wdt=Wdt(),
        reset=lambda: reset.append(True),
        clock=Clock(),
        out=lambda line: None,
        sleep=lambda ms: None,
    )

    assert result == "provisioned"
    assert reset == [True]
    assert store.boot_count() == 0
    assert screens[-1] == "IDLE"


def test_401_in_the_link_thread_reboots_into_setup(tmp_path, monkeypatch):
    _record_screens(monkeypatch)
    reset = []
    store = _provisioned(tmp_path)
    factory = LinkFactory(fail=link_module.NeedsProvisioning("gone"))

    result = main.run(
        store=store,
        sta=Sta(),
        panel=Panel(),
        wdt=Wdt(),
        reset=lambda: reset.append(True),
        thread_start=lambda target, args: target(*args),
        link_factory=factory,
        clock=Clock(),
        out=lambda line: None,
        sleep=lambda ms: None,
        ticks_normal=3,
    )

    assert result == "provision"
    assert reset == [True]
    assert factory.links[0].closed is True


def test_three_wifi_failures_open_ble_setup(tmp_path, monkeypatch):
    _record_screens(monkeypatch)
    logs = []
    setup_calls = []

    def fake_setup(*args, **kwargs):
        setup_calls.append(True)
        return "setup"

    monkeypatch.setattr(main, "run_setup", fake_setup)
    sta = Sta(error=net.WifiError("no address"))

    result = main.run(
        store=_provisioned(tmp_path),
        sta=sta,
        panel=Panel(),
        wdt=Wdt(),
        reset=lambda: None,
        link_factory=LinkFactory(),
        clock=Clock(),
        out=logs.append,
        sleep=lambda ms: None,
    )

    assert result == "setup"
    assert len(sta.connects) == main.WIFI_ATTEMPTS == 3
    assert logs[-1] == "wifi failed 3x; ble setup"


def test_wifi_is_retried_three_times_then_gives_up():
    sta = Sta(error=net.WifiError("wrong password"))
    logs = []

    assert main.connect_wifi(sta, _config(), out=logs.append) is None
    assert len(sta.connects) == 3
    assert logs == ["wifi=failed"]


def test_watchdog_is_fed_every_tick(tmp_path, monkeypatch):
    _record_screens(monkeypatch)
    wdt = Wdt()
    link_factory = LinkFactory()

    result = main.run_normal(
        _provisioned(tmp_path),
        Sta(),
        Panel(),
        _config(),
        wdt=wdt,
        thread_start=lambda target, args: None,
        link_factory=link_factory,
        clock=Clock(),
        out=lambda line: None,
        sleep=lambda ms: None,
        ticks=7,
    )

    assert result == "ticks"
    assert wdt.feeds >= 7


def test_run_creates_the_station_when_none_is_injected(tmp_path, monkeypatch):
    _record_screens(monkeypatch)
    created = []

    def station():
        sta = Sta()
        created.append(sta)
        return sta

    monkeypatch.setattr(main.net, "Sta", station)

    result = main.run(
        store=_provisioned(tmp_path),
        panel=Panel(),
        wdt=Wdt(),
        reset=lambda: None,
        thread_start=lambda target, args: target(*args),
        link_factory=LinkFactory(),
        clock=Clock(),
        out=lambda line: None,
        sleep=lambda ms: None,
        ticks_normal=2,
    )

    assert result == "stopped"
    assert len(created) == 1
    assert created[0].connects


def test_connect_wifi_without_a_radio_fails_cleanly():
    logs = []
    assert main.connect_wifi(None, _config(), out=logs.append) is None
    assert logs == ["wifi=failed"]


def test_main_has_no_access_point_or_portal():
    source = pathlib.Path(main.__file__).read_text(encoding="utf-8")
    assert "portal" not in source.lower()
    assert "net.Ap" not in source
    assert "create_ap" not in source


def _wifi_only(tmp_path):
    store = _store(tmp_path)
    store.save_config({"wifi": {"ssid": "home", "password": "secret123"}})
    return store


class Page:
    """A pairing page that reports paired on the n-th poll."""

    def __init__(self, paired_on=None):
        self.paired_on = paired_on
        self.polls = 0
        self.started = False
        self.closed = False
        self.made = None

    def __call__(self, handler, code, defaults, out=print):
        self.made = (handler, code, defaults)
        return self

    def start(self):
        self.started = True

    def poll(self):
        self.polls += 1
        return self.paired_on is not None and self.polls >= self.paired_on

    def close(self):
        self.closed = True


def test_wifi_without_a_key_boots_into_the_pairing_page(tmp_path, monkeypatch):
    _record_screens(monkeypatch)
    calls = []
    monkeypatch.setattr(main, "run_pair", lambda *a, **k: calls.append(a) or "ticks")
    monkeypatch.setattr(main, "run_setup", lambda *a, **k: "setup")

    result = main.run(
        store=_wifi_only(tmp_path),
        sta=Sta(),
        panel=Panel(),
        wdt=Wdt(),
        reset=lambda: None,
        clock=Clock(),
        out=lambda line: None,
        sleep=lambda ms: None,
    )

    assert result == "ticks"
    assert len(calls) == 1


def test_the_pairing_page_falls_back_to_bluetooth_without_wifi(tmp_path, monkeypatch):
    _record_screens(monkeypatch)
    logs = []
    monkeypatch.setattr(main, "run_setup", lambda *a, **k: "setup")

    result = main.run(
        store=_wifi_only(tmp_path),
        sta=Sta(error=net.WifiError("gone")),
        panel=Panel(),
        wdt=Wdt(),
        reset=lambda: None,
        clock=Clock(),
        out=logs.append,
        sleep=lambda ms: None,
    )

    assert result == "setup"
    assert "wifi failed 3x; ble setup" in logs


def test_pairing_shows_the_address_serves_and_reboots_when_paired(tmp_path, monkeypatch):
    _record_screens(monkeypatch)
    logs = []
    resets = []
    page = Page(paired_on=3)
    store = _wifi_only(tmp_path)
    store.set_boot_count(2)

    result = main.run_pair(
        store,
        Sta(),
        Panel(),
        store.load_config(),
        code="4821",
        page_factory=page,
        wdt=Wdt(),
        reset=lambda: resets.append(True),
        clock=Clock(),
        out=logs.append,
        sleep=lambda ms: None,
    )

    assert result == "paired"
    assert page.started and page.closed and page.polls == 3
    assert page.made[1] == "4821"
    assert "pair page http://heare-panel.local ip=192.168.1.50" in logs
    assert resets == [True]
    assert store.boot_count() == 0


def test_show_pair_fits_the_address_and_code_in_the_window():
    panel = Panel()

    assert main.show_pair(panel, "4821", Framebuf()) is True

    # 72 px wide: no line may run past the window's right edge.
    for op in panel.viewports[0].canvas.ops:
        if op[0] == "text":
            assert op[2] - 28 + len(op[1]) * 8 <= 72, op
    assert ("text", "4821", 28 + 20, 24 + 31, 1) in panel.viewports[0].canvas.ops


def test_an_old_key_with_a_wifi_only_config_still_pairs(tmp_path, monkeypatch):
    _record_screens(monkeypatch)
    calls = []
    monkeypatch.setattr(main, "run_pair", lambda *a, **k: calls.append(a) or "ticks")
    store = _wifi_only(tmp_path)
    store.set_part_token("left-from-an-earlier-pairing")

    main.run(
        store=store,
        sta=Sta(),
        panel=Panel(),
        wdt=Wdt(),
        reset=lambda: None,
        clock=Clock(),
        out=lambda line: None,
        sleep=lambda ms: None,
    )

    assert len(calls) == 1


def test_live_states_show_the_face_and_setup_states_the_word():
    for word in ("IDLE", "HEAR", "THINK", "SPEAK"):
        panel = Panel()
        assert main.show_state(panel, word, 0, Framebuf()) is True
        assert not [op for op in panel.viewports[0].canvas.ops if op[0] == "text"]


def test_the_face_redraws_only_when_its_frame_changes():
    panel = Panel()

    assert main.frame_key("IDLE", panel, 0) == main.frame_key("IDLE", panel, 3000)
    assert main.frame_key("IDLE", panel, 0) != main.frame_key("IDLE", panel, 5100)
    assert main.frame_key("SPEAK", panel, 0) != main.frame_key("SPEAK", panel, 500)
    assert main.frame_key("WIFI", panel, 0) == main.frame_key("WIFI", panel, 9000)
