"""Boot the panel: Bluetooth for Wi-Fi, its own page for the brain, then work.

Three modes, chosen at boot from what is stored:

* no Wi-Fi config (or three boots that never got healthy): BLE setup. The
  panel advertises ``heare-panel-XXXX`` for ten minutes with a four-digit code
  on the screen; the page sets Wi-Fi only, and a save that joins resets.
* Wi-Fi but no part key: the pairing page. The panel joins Wi-Fi and serves
  ``http://heare-panel.local`` with a new code on the screen; pairing there
  resets into work.
* both: normal operation, the link to the brain.

A display that is missing or dies later never stops the network side: every
draw is best effort. The module is import-safe on a laptop, so ``machine``,
``framebuf`` and ``_thread`` are imported only inside the functions that need
them and ``run`` accepts every dependency for the host tests.

The import order is not cosmetic. On this build BLE needs a large contiguous
block, and loading ``link``/``state``/``provision`` before ``BleSetup.start``
leaves too little for the controller (``OSError EIO``). The setup path starts
BLE with only the radio and display modules loaded, then imports the
provisioning transaction once advertising is up and the heap has settled.
"""

import gc
import time

from heare_panel import ble_setup, clock, display as panel_display, net
from heare_panel import journal

# Mirrors of store.py's persisted names, used only for the pre-BLE check.
# Importing store/config here costs ~10 KB and makes BleSetup.start fail with
# EIO on this board, so the real Store is created after BLE is advertising.
NVS_NAMESPACE = "heare"
KEY_PART_TOKEN = "part_token"
KEY_BOOT_COUNT = "boot_count"
CONFIG_PATH = "/config.json"
# A failed Bluetooth save reboots and comes back with the same code and its
# result; this file carries both across the reset and is read once.
RETRY_PATH = "/setup_retry.json"

BOOT_LIMIT = 3
WDT_MS = 30_000
HEALTHY_MS = 60_000
# Short enough for the face to move at ~8 frames a second; a frame is
# drawn only when render.frame_key changes, so still states cost nothing.
TICK_MS = 100
BLE_WINDOW_MS = 600_000
WIFI_ATTEMPTS = 3
SETUP_NAME = "heare-panel-"
SETUP_TITLE = "SETUP"
BLINK_MS = 500
# The BOOT button on ESP32-C3 boards; pulled up, so pressed reads 0.
BUTTON_PIN = 9


def _nvs():
    try:
        import esp32

        return esp32.NVS(NVS_NAMESPACE)
    except Exception:  # noqa: BLE001 - no NVS means no provisioning state
        return None


def _blob(nvs, key):
    try:
        return nvs.get_blob(key)
    except TypeError:
        buffer = bytearray(64)
        try:
            length = nvs.get_blob(key, buffer)
        except OSError:
            return None
        if length is None:
            return bytes(buffer)
        return bytes(buffer[:length])
    except OSError:
        return None


def _text(nvs, key):
    data = _blob(nvs, key)
    if data is None:
        return None
    try:
        return bytes(data).decode()
    except (TypeError, ValueError):
        return None


def _number(nvs, key):
    value = _text(nvs, key)
    if value is None:
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _has_config(path=CONFIG_PATH):
    try:
        with open(path) as handle:
            handle.read(1)
    except OSError:
        return False
    return True


def _record_boot_nvs():
    nvs = _nvs()
    if nvs is None:
        return
    try:
        nvs.set_blob(KEY_BOOT_COUNT, str(_number(nvs, KEY_BOOT_COUNT) + 1).encode())
        nvs.commit()
    except OSError:
        pass


def _quiet_system():
    """The chip's own log off the USB port, unless the panel is debugged.

    ESP-IDF writes Wi-Fi and system messages to the USB port. On a Mac's USB
    with nobody reading it, a write waits for room, and the panel stalled
    before reaching the brain (26 October). A panel on a charger never saw it."""
    if journal.loud():
        return
    try:
        import esp

        esp.osdebug(None)
    except (ImportError, AttributeError):
        pass


FRESH_PATH = "/fresh_code"


def _fresh_code(path=None):
    """Whether a deploy left its mark; the mark is taken away either way."""
    import os

    try:
        os.remove(path or FRESH_PATH)
    except OSError:
        return False
    return True


def _clear_boot_nvs():
    nvs = _nvs()
    if nvs is None:
        return
    try:
        nvs.set_blob(KEY_BOOT_COUNT, b"0")
        nvs.commit()
    except OSError:
        pass


def _setup_required_nvs():
    # A missing part key is not a Bluetooth boot: with Wi-Fi stored, the
    # panel pairs from its own page.
    if not _has_config():
        return True
    nvs = _nvs()
    if nvs is None:
        return True
    return _number(nvs, KEY_BOOT_COUNT) >= BOOT_LIMIT


def _open_store():
    from heare_panel import store as storage

    return storage.Store()


def setup_required(store):
    """Whether this boot must offer BLE setup.

    Three failures to get online leave the boot counter high on purpose: a
    panel that cannot reach its brain after three resets is easier to fix
    through the setup page than through another silent reboot.
    """
    try:
        if store.load_config() is None:
            return True
        return store.boot_count() >= BOOT_LIMIT
    except Exception:  # noqa: BLE001 - a broken store must still offer setup
        return True


def record_boot(store):
    try:
        store.set_boot_count(store.boot_count() + 1)
    except (OSError, AttributeError, ValueError):
        pass


def clear_boot(store):
    try:
        store.set_boot_count(0)
    except (OSError, AttributeError, ValueError):
        pass


def board_tag():
    try:
        import machine

        mac = machine.unique_id()
    except Exception:  # noqa: BLE001 - identity is never a boot gate
        mac = None
    if not mac:
        try:
            import network

            mac = network.WLAN(network.STA_IF).config("mac")
        except Exception:  # noqa: BLE001 - identity is never a boot gate
            mac = None
    if not mac:
        return "0000"
    try:
        return "".join("{:02x}".format(byte) for byte in mac)[-4:]
    except (TypeError, ValueError):
        return "0000"


def _take_retry(path=RETRY_PATH):
    """The code and result a failed save left for this boot, once."""
    import json
    import os

    try:
        with open(path) as handle:
            data = json.loads(handle.read())
    except (OSError, ValueError):
        return None, None
    try:
        os.remove(path)
    except OSError:
        pass
    code = data.get("code") if isinstance(data, dict) else None
    result = data.get("result") if isinstance(data, dict) else None
    if not (isinstance(code, str) and len(code) == 4 and code.isdigit()):
        return None, None
    return code, result if isinstance(result, dict) else None


def _keep_retry(code, result, path=RETRY_PATH):
    import json

    try:
        with open(path, "w") as handle:
            handle.write(json.dumps({"code": code, "result": result}))
    except OSError:
        pass


def setup_name(tag=None):
    return SETUP_NAME + (tag or board_tag())


def _sleep(ms):
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(ms)
    else:
        time.sleep(ms / 1000)


def _make_wdt(timeout=WDT_MS):
    try:
        import machine

        return machine.WDT(timeout=timeout)
    except Exception:  # noqa: BLE001 - the host has no watchdog
        return None


def _reboot(reset=None):
    if reset is not None:
        try:
            reset()
        except Exception:  # noqa: BLE001 - a reset callback is untrusted
            pass
        return
    try:
        import machine

        machine.reset()
    except Exception:  # noqa: BLE001 - the host cannot reset
        pass


def _feed(wdt):
    if wdt is None:
        return
    try:
        wdt.feed()
    except Exception:  # noqa: BLE001 - a watchdog must never stop the loop
        pass


def _canvas(panel, framebuf_module=None):
    try:
        if framebuf_module is None:
            import framebuf as framebuf_module
        data = bytearray(panel.width * panel.height // 8)
        buffer = framebuf_module.FrameBuffer(
            data, panel.width, panel.height, framebuf_module.MONO_VLSB
        )
        canvas = panel.viewport(buffer)
        if canvas is None:
            return None, None
        return data, canvas
    except Exception:  # noqa: BLE001 - drawing is never a boot gate
        return None, None


def _flush(panel, data):
    try:
        return bool(panel.flush(data))
    except (OSError, ValueError, AttributeError):
        return False


def _paint(panel, scene, now_ms=0, framebuf_module=None):
    """Draw one scene on the window: the one way anything reaches it."""
    if panel is None or not getattr(panel, "available", False):
        return False
    data, canvas = _canvas(panel, framebuf_module)
    if canvas is None:
        return False
    try:
        scene.draw(canvas, panel.view_width, panel.view_height, now_ms)
    except (ValueError, AttributeError, IndexError):
        return False
    return _flush(panel, data)


def _draw_lines(panel, lines, framebuf_module=None):
    from heare_panel import screen

    scene = screen.Scene("lines", {"widget": "text", "lines": [t for t, _y in lines],
                                   "at": [y for _t, y in lines]})
    return _paint(panel, scene, 0, framebuf_module)


def show_boot(panel, framebuf_module=None):
    return _draw_lines(panel, [("BOOT", 8)], framebuf_module)


def show_setup(panel, code, framebuf_module=None):
    return _draw_lines(
        panel, [(SETUP_TITLE, 4), (code, 20)], framebuf_module
    )


def show_pair(panel, code, framebuf_module=None):
    """The page's address and the code, in a 9-character-wide window."""
    return _draw_lines(
        panel,
        [("heare-", 0), ("panel", 10), (".local", 20), (code, 31)],
        framebuf_module,
    )


SYNC_MS = 6 * 3600 * 1000
RETRY_SYNC_MS = 300_000
_tried = [None]


def _due(now, clock=clock):
    """Whether a failed sync may be tried again: not every tick."""
    last = _tried[0]
    if last is None or clock.elapsed(last, now) >= RETRY_SYNC_MS:
        _tried[0] = now
        return True
    return False


def _sync_time(out=journal.say):
    """The board's clock set from the network; False when it could not be."""
    try:
        import ntptime

        ntptime.settime()
    except Exception as error:  # noqa: BLE001 - a clock is never a boot gate
        out("time=unsynced " + type(error).__name__)
        return False
    out("time=synced")
    return True


def _memory():
    """Free memory, after a collection: what the screen has to work with."""
    import gc

    gc.collect()
    # CPython's gc has no mem_free; a host test reads -1.
    return gc.mem_free() if hasattr(gc, "mem_free") else -1


def _system_memory():
    """ESP-IDF's own free heap: what Wi-Fi and every socket live on, apart
    from Python's. When it runs out, the panel stops answering even a ping."""
    try:
        import esp32

        return sum(block[1] for block in esp32.idf_heap_info(esp32.HEAP_DATA))
    except (ImportError, AttributeError):
        return -1


def show_text(panel, lines, framebuf_module=None):
    """Words alone on the window, a line every ten pixels."""
    from heare_panel import screen

    return _paint(panel, screen.text_scene(lines), 0, framebuf_module)


def show_picture(panel, bits, framebuf_module=None):
    """A picture alone on the window: rows, eight pixels a byte."""
    from heare_panel import screen

    return _paint(panel, screen.picture_scene(bits), 0, framebuf_module)


def frame_key(word, panel, now_ms):
    """What tells one frame of a status word from the next."""
    from heare_panel import widgets

    box = (0, 0, getattr(panel, "view_width", 0), getattr(panel, "view_height", 0))
    return widgets.key({"widget": "state", "word": word}, {}, now_ms, box)


def show_state(panel, word, now_ms=0, framebuf_module=None):
    from heare_panel import screen

    return _paint(panel, screen.state_scene(word), now_ms, framebuf_module)


def connect_wifi(sta, config, attempts=WIFI_ATTEMPTS, wdt=None, out=journal.say):
    """Join the stored network, retrying a bounded number of times.

    Each association is at most the adapter's timeout (15 s by default), so
    the main thread never blocks a watchdog interval; the watchdog is fed
    between attempts anyway because the display task owns it.
    """
    if sta is None:
        out("wifi=failed")
        return None
    wifi = config.get("wifi") or {}
    ssid = wifi.get("ssid", "")
    password = wifi.get("password", "")
    for _attempt in range(attempts):
        _feed(wdt)
        try:
            ip = sta.connect(ssid, password)
        except (net.WifiError, OSError, ValueError):
            ip = None
        if ip:
            out("wifi=up ip=" + str(ip))
            return ip
        _feed(wdt)
    out("wifi=failed")
    return None


def supervise(link, outcome, out=journal.say):
    from heare_panel import link as link_module

    try:
        link.supervise()
        outcome.append("stopped")
    except link_module.NeedsProvisioning:
        outcome.append("provision")
    except Exception as error:  # noqa: BLE001 - the reason is reported, not raised
        outcome.append("error")
        out("link=error " + type(error).__name__)


def _start_thread(target, args):
    import _thread

    _thread.start_new_thread(target, args)


# What the panel's state is called where the voice reads it: words, not codes.
_SAID = {"BOOT": "starting", "WIFI": "looking for Wi-Fi", "PAIR": "waiting to pair",
         "BRAIN": "looking for the brain", "IDLE": "face", "HEAR": "listening",
         "THINK": "thinking", "SPEAK": "speaking", "ALLOW": "asking leave",
         "ERROR": "error", "OFFLINE": "offline"}


def _report(link, word, outcome):
    from heare_panel import link as link_module

    try:
        link.report({"now": _SAID.get(word, word)})
    except link_module.NeedsProvisioning:
        outcome.append("provision")
    except Exception:  # noqa: BLE001 - a report failure is not fatal
        return


def _close(link):
    try:
        link.close()
    except Exception:  # noqa: BLE001 - closing is best effort
        pass


def run_normal(
    store,
    sta,
    panel,
    config,
    wdt=None,
    reset=None,
    thread_start=None,
    link_factory=None,
    clock=clock,
    out=journal.say,
    sleep=None,
    ticks=None,
    attempts=WIFI_ATTEMPTS,
    button=None,
    device=None,
):
    """Join Wi-Fi, start one ``Link`` thread, and render what it shows.

    Returns ``wifi`` when three associations failed, ``provision`` when the
    brain no longer knows the key, ``error`` for another link failure, and
    ``ticks`` when a test asked for a bounded number of iterations.
    """
    from heare_panel import link as link_module
    from heare_panel import render, state

    sleep = sleep or _sleep
    show_state(panel, render.WIFI, clock.now_ms())
    out("screen=WIFI")

    if connect_wifi(sta, config, attempts, wdt, out) is None:
        return "wifi"
    synced_at = clock.now_ms() if _sync_time(out) else None

    factory = link_factory or link_module.Link
    try:
        link = factory(config, store, clock=clock, on_state=None)
    except Exception:  # noqa: BLE001 - a failed link is a recoverable boot
        out("link=failed")
        return "error"

    from heare_panel import device as device_module
    from heare_panel import widgets

    board = device if device is not None else device_module.Device(
        device_module.board_pin
    )
    link.on_call = lambda name, args: board.call(name, args, clock.now_ms())
    if board.restore():
        out("idle screen restored")

    initial = getattr(getattr(link, "display", None), "state", render.BRAIN)
    show_state(panel, initial, clock.now_ms())
    out("screen=" + initial)

    outcome = []
    starter = thread_start or _start_thread
    starter(supervise, (link, outcome, out))

    pressed = button if button is not None else _make_button()
    was_pressed = True  # a button held through boot is not a yes
    last_key = None
    last_word = None
    online_since = None
    boot_cleared = False
    slowest = 0
    count = 0
    while ticks is None or count < ticks:
        count += 1
        _feed(wdt)
        now = clock.now_ms()
        try:
            link.display = state.reduce(link.display, {}, now)
        except (AttributeError, ValueError):
            _close(link)
            return "error"
        word = link.display.state

        down = bool(pressed()) if pressed is not None else False
        if down and not was_pressed:
            _press(link, out)
        was_pressed = down

        board.tick(now)
        offset = getattr(link, "utc_offset", None)
        if isinstance(offset, int):
            widgets.UTC_OFFSET_S = offset
        if synced_at is None or clock.elapsed(synced_at, now) >= SYNC_MS:
            # A clock that drifts is set again from the network now and then;
            # one that never was is tried every few minutes until it is.
            if synced_at is None and not _due(now, clock):
                pass
            elif _sync_time(out):
                synced_at = now
            else:
                synced_at = None
        # The screen knows what is seen; the loop only draws when it changed.
        board.screen.word(word)
        key = board.screen.key(now)
        if key != last_key:
            drawn_at = clock.now_ms()
            _paint(panel, board.screen, now)
            slowest = max(slowest, clock.elapsed(drawn_at, clock.now_ms()))
            last_key = key
        if word != last_word:
            _report(link, word, outcome)
            out("screen=" + word)
            last_word = word

        online = getattr(link.display, "link", "") == "online"
        if online:
            if online_since is None:
                online_since = now
                out("link=online mem free=" + str(_memory())
                    + " idf=" + str(_system_memory()))
            elif not boot_cleared and clock.elapsed(online_since, now) >= HEALTHY_MS:
                clear_boot(store)
                boot_cleared = True
                out("healthy=60s slowest frame ms=" + str(slowest)
                    + " mem free=" + str(_memory())
                    + " idf=" + str(_system_memory()))
        else:
            online_since = None

        if outcome:
            reason = outcome[0]
            _close(link)
            if reason == "stopped":
                return "stopped"
            if reason == "provision":
                out("needs provisioning")
                return "provision"
            return "error"

        sleep(TICK_MS)

    _close(link)
    return "ticks"


def run_pair(
    store,
    sta,
    panel,
    config,
    code=None,
    page_factory=None,
    link_factory=None,
    wdt=None,
    reset=None,
    clock=clock,
    out=journal.say,
    sleep=None,
    framebuf_module=None,
    ticks=None,
    attempts=WIFI_ATTEMPTS,
):
    """Join Wi-Fi and serve the pairing page until the brain gives a key.

    Returns ``wifi`` when the stored network cannot be joined (the caller
    falls back to Bluetooth), ``paired`` after a reset into work, and
    ``ticks`` when a test bounded the loop.
    """
    from heare_panel import link as link_module
    from heare_panel import pair_web
    from heare_panel.provision import Provisioner

    sleep = sleep or _sleep
    show_state(panel, "WIFI", clock.now_ms(), framebuf_module)
    out("screen=WIFI")
    ip = connect_wifi(sta, config, attempts, wdt, out)
    if ip is None:
        return "wifi"
    # Wi-Fi works, so these boots are not the failures the counter is for.
    clear_boot(store)

    provisioner = Provisioner(
        store, sta, link_factory or link_module.Link, clock=clock, out=out
    )
    code = code or ble_setup.make_code()
    device = config.get("device") or {}
    brain = config.get("brain") or {}
    page = (page_factory or pair_web.PairPage)(
        provisioner.handle,
        code,
        {
            "brain_host": brain.get("host"),
            "brain_port": brain.get("port"),
            "name": device.get("name"),
        },
        out=out,
    )
    try:
        page.start()
    except OSError as error:
        out("pair page failed " + type(error).__name__)
        return "wifi"
    shown = show_pair(panel, code, framebuf_module)
    out("pair page http://" + net.HOSTNAME + ".local ip=" + str(ip))
    out("code=" + code)
    out("screen=PAIR" if shown else "screen=none")

    count = 0
    while ticks is None or count < ticks:
        count += 1
        _feed(wdt)
        if page.poll():
            # Let the browser receive its answer before the radio goes away.
            sleep(1000)
            page.close()
            show_state(panel, "IDLE", clock.now_ms(), framebuf_module)
            out("paired")
            _reboot(reset)
            return "paired"
        sleep(TICK_MS)
    page.close()
    return "ticks"


def _make_button():
    """A ``pressed()`` callable for the BOOT button, or None off the board."""
    try:
        import machine

        pin = machine.Pin(BUTTON_PIN, machine.Pin.IN, machine.Pin.PULL_UP)
    except Exception:  # noqa: BLE001 - no button is not a boot gate
        return None
    return lambda: pin.value() == 0


def _press(link, out):
    """The button is the person's yes, and only for an open permission."""
    display = getattr(link, "display", None)
    allow_id = getattr(display, "allow_id", None)
    if getattr(display, "allow_kind", None) != "permission" or allow_id is None:
        return
    try:
        answered = link.permit(allow_id)
    except Exception:  # noqa: BLE001 - a lost yes must not stop the panel
        answered = False
    out("button yes " + ("given" if answered else "not taken"))


def _token(store):
    try:
        return store.get_part_token()
    except (OSError, AttributeError, ValueError):
        return None


def _saved(provisioner, store, before_token):
    if getattr(provisioner, "ip", "") and not getattr(provisioner, "error", ""):
        return True
    token = _token(store)
    return bool(token) and token != before_token


def _not_ready(command):
    return {"ok": False, "error": "setup"}


def _placeholder_status(name):
    return {"state": "setup", "name": name}


def run_setup(
    store,
    sta,
    panel,
    tag=None,
    code=None,
    ble_factory=None,
    provisioner=None,
    wdt=None,
    reset=None,
    clock=clock,
    out=journal.say,
    sleep=None,
    framebuf_module=None,
    max_ticks=None,
    window_ms=BLE_WINDOW_MS,
):
    """Advertise the BLE setup service until a save succeeds or the window ends.

    The handler becomes exactly ``Provisioner.handle``; status and scan are
    the two providers ``BleSetup`` reads. Success stops BLE, clears the failed
    boot counter, shows ``IDLE`` and resets into normal operation.

    With no injected provisioner the transaction module is imported only
    after ``start()``. That is what keeps the BLE controller's allocation
    contiguous on this board; the placeholder handler and status answer the
    one publish that ``start()`` performs.
    """
    sleep = sleep or _sleep
    name = setup_name(tag)
    carried = None
    if code is None:
        code, carried = _take_retry()
    code = code or ble_setup.make_code()

    def after_failed_save(result):
        _keep_retry(code, result)
        out("save failed; rebooting to report it")
        _reboot(reset)

    if provisioner is None:
        handler = _not_ready

        def status_provider():
            return _placeholder_status(name)

        def scan_provider():
            return []
    else:
        handler = provisioner.handle

        def status_provider():
            return provisioner.handle({"cmd": "status"})

        def scan_provider():
            return sta.scan() if sta is not None else []

    factory = ble_factory or ble_setup.BleSetup
    setup = factory(
        name,
        handler,
        code,
        window_ms=window_ms,
        status_provider=status_provider,
        scan_provider=scan_provider,
        out=out,
        release_wifi=getattr(sta, "release", None),
        after_failed_save=after_failed_save,
        carried_result=carried,
    )

    gc.collect()
    try:
        setup.start()
    except Exception as error:  # noqa: BLE001 - no radio is reported
        out("ble=failed " + type(error).__name__)
        out("ble=failed detail=" + str(error)[:80])
        return "ble-failed"

    shown = show_setup(panel, code, framebuf_module)
    out("screen=" + SETUP_TITLE if shown else "screen=none")
    gc.collect()

    if provisioner is None:
        try:
            if store is None:
                store = _open_store()
            from heare_panel import link as link_module
            from heare_panel.provision import Provisioner

            provisioner = Provisioner(
                store, sta, link_module.Link, clock=clock, out=out
            )
        except Exception as error:  # noqa: BLE001 - no transaction, no setup
            out("setup failed " + type(error).__name__)
            setup.stop()
            return "setup-failed"
        setup.handler = provisioner.handle
        setup.status_provider = lambda: provisioner.handle({"cmd": "status"})
        setup.scan_provider = lambda: sta.scan() if sta is not None else []

    before_token = _token(store)
    count = 0
    while getattr(setup, "running", False):
        _feed(wdt)
        setup.poll()
        if not getattr(setup, "running", False):
            break
        count += 1
        if max_ticks is not None and count >= max_ticks:
            break
        sleep(TICK_MS)

    setup.stop()
    if _saved(provisioner, store, before_token):
        clear_boot(store)
        show_state(panel, "IDLE", clock.now_ms(), framebuf_module)
        out("provisioned")
        out("screen=IDLE")
        gc.collect()
        _reboot(reset)
        return "provisioned"

    out("ble window closed")
    if max_ticks is not None:
        return "ticks"
    _reboot(reset)
    return "timeout"


def run(
    store=None,
    sta=None,
    panel=None,
    wdt=None,
    reset=None,
    thread_start=None,
    ble_factory=None,
    link_factory=None,
    clock=clock,
    out=journal.say,
    sleep=None,
    framebuf_module=None,
    ticks_setup=None,
    ticks_normal=None,
    tag=None,
    code=None,
):
    _quiet_system()
    if _fresh_code():
        # The code was just replaced: earlier failed boots were the old
        # code's, not a reason to send the new one to Bluetooth setup.
        if store is not None:
            clear_boot(store)
        else:
            _clear_boot_nvs()
        out("fresh code: boot count cleared")
    if store is not None:
        record_boot(store)
        needed = setup_required(store)
    else:
        _record_boot_nvs()
        needed = _setup_required_nvs()

    if sta is None:
        try:
            sta = net.Sta()
        except Exception:  # noqa: BLE001 - no radio is still a setup boot
            sta = None
    if panel is None:
        panel = panel_display.open()
    if wdt is None:
        wdt = _make_wdt()

    show_boot(panel, framebuf_module)
    out("BOOT")

    if needed:
        result = run_setup(
            store,
            sta,
            panel,
            tag=tag,
            code=code,
            ble_factory=ble_factory,
            wdt=wdt,
            reset=reset,
            clock=clock,
            out=out,
            sleep=sleep,
            framebuf_module=framebuf_module,
            max_ticks=ticks_setup,
        )
        if result != "ble-failed":
            return result
        # Setup that cannot start is no reason to stand frozen on BOOT: the
        # saved settings get another go (26 October: EIO, stuck until reflash).
        if store is not None:
            clear_boot(store)
        else:
            _clear_boot_nvs()
        out("ble failed; trying the saved settings")

    if store is None:
        try:
            store = _open_store()
        except Exception:  # noqa: BLE001 - no store means setup
            store = None
    try:
        config = store.load_config() if store is not None else None
    except Exception:  # noqa: BLE001 - an unreadable config means setup
        config = None
    # A key left from an earlier pairing is no use without a brain to use it
    # on: a Wi-Fi-only config always means the pairing page.
    if config is not None and ("brain" not in config or _token(store) is None):
        result = run_pair(
            store,
            sta,
            panel,
            config,
            code=code,
            link_factory=link_factory,
            wdt=wdt,
            reset=reset,
            clock=clock,
            out=out,
            sleep=sleep,
            framebuf_module=framebuf_module,
            ticks=ticks_setup,
        )
        if result != "wifi":
            return result
        out("wifi failed 3x; ble setup")
        config = None
    if config is None:
        return run_setup(
            store,
            sta,
            panel,
            tag=tag,
            code=code,
            ble_factory=ble_factory,
            wdt=wdt,
            reset=reset,
            clock=clock,
            out=out,
            sleep=sleep,
            framebuf_module=framebuf_module,
            max_ticks=ticks_setup,
        )

    result = run_normal(
        store,
        sta,
        panel,
        config,
        wdt=wdt,
        reset=reset,
        thread_start=thread_start,
        link_factory=link_factory,
        clock=clock,
        out=out,
        sleep=sleep,
        ticks=ticks_normal,
    )
    if result == "wifi":
        out("wifi failed 3x; ble setup")
        return run_setup(
            store,
            sta,
            panel,
            tag=tag,
            code=code,
            ble_factory=ble_factory,
            clock=clock,
            out=out,
            sleep=sleep,
            framebuf_module=framebuf_module,
            max_ticks=ticks_setup,
        )
    if result in ("provision", "error"):
        _reboot(reset)
    return result


if __name__ == "__main__":
    run()
