"""The panel's one connection to the brain: pair, knock, listen, come back.

A part's life in the brain is short to describe and easy to get wrong. A
machine pairs once with a one-time code and gets a key that may only knock.
The knock names the panel explicitly and is approved by the machine key; the
answer carries a part key that may listen and report. After that the part
key is the only secret kept: the machine key exists for one request and is
never written anywhere.

The panel knocks before *every* stream. The brain forgets its parts when it
restarts, and a stream opened without a knock is silent forever; a knock is
cheap next to a black screen. A stream that goes quiet for ``SILENCE_S`` is
taken for dead, and one refused with ``401`` is final: the key is gone, the
panel says ``PAIR``, and whoever runs it opens the setup portal again.

Nothing here logs a key or conversation text. The socket is injected
(``socket_factory``) and so are the clock and the store, so the same code is
driven by the board and by a fake in ``tests/firmware``.
"""

import socket
import time

from heare_panel import clock, device, http, sse, state

PROTOCOL = 1
VERSION = "0.1.0"

ABOUT = "status panel: OLED, a light and nine free pins"
UI = [{"name": "now", "kind": "label", "label": "Screen",
       "value": "BOOT", "head": True}]

EVENT_KINDS = ("phase,permission,permission_closed,question,"
               "question_closed,said,failed,cannot,control")

BACKOFF_S = (1, 2, 4, 8, 15, 30)
THROTTLED_S = 60
SILENCE_S = 15.0
CONNECT_S = 5.0
READ = 512


class LinkError(Exception):
    """The brain could not be reached or refused the panel."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NeedsProvisioning(LinkError):
    """The part key is gone; a person must pair the panel again."""


class Throttled(LinkError):
    """The brain asked for a pause before the next attempt."""

    def __init__(self, message: str, retry_s: int = THROTTLED_S) -> None:
        super().__init__(message, 429)
        self.retry_s = retry_s


class Link:
    """One connection task's worth of state: keys, display, sockets.

    ``config`` is a validated config dict, ``store`` is the panel's NVS
    store. The panel's own name is taken from ``store.get_pid()`` once it has
    paired; before that ``config["device"]["name"]`` must already be the
    stable host (``esp32c3-<mac4>``), which the portal writes from
    ``config.identity``. Nothing user-entered ever reaches a pid.
    """

    def __init__(self, config: dict, store,
                 socket_factory=socket.socket, clock=clock, on_state=None) -> None:
        self.config = config
        self.store = store
        self.socket_factory = socket_factory
        self.clock = clock
        self.on_state = on_state
        self.brain_host = config["brain"]["host"]
        self.port = int(config["brain"]["port"])
        self.channel, self.host, self.pid = self._identity(config, store)
        self.display = state.initial("brain")
        self._sock = None
        self._closed = False
        self._last_values = None
        self.silence_s = SILENCE_S
        self.auto_ap = False
        self.needs_provisioning = store.get_part_token() is None
        self.waits: list[float] = []
        # Called with (name, value) for a control addressed to this panel.
        self.on_control = None

    def pair(self, code: str) -> str:
        """Redeem a one-time code, then exchange the machine key for a part key.

        The machine key is held in the two locals below only; both are
        cleared as soon as the knock returns. What survives is the part key
        in ``store`` and the pid that names it.
        """
        normalized = str(code or "").strip().upper()
        try:
            status, answer = self._post("/api/pair", "", {
                "code": normalized,
                "kind": "machine",
                "name": self.host,
            })
        except http.HttpError as error:
            status, answer = error.status, {}
        if status == 429:
            raise Throttled("pairing is rate limited")
        if status in (401, 403):
            raise LinkError("the pair code was refused", status)
        if status != 200:
            raise LinkError(f"pairing failed with status {status}", status)

        machine_pid = answer.get("pid")
        machine_token = answer.get("token")
        if not isinstance(machine_pid, str) or not isinstance(machine_token, str):
            raise LinkError("the pairing answer carried no key")
        if machine_pid != "machine@" + self.host:
            raise LinkError("the brain named another machine")

        try:
            knocked = self.knock(machine_token, part=False)
        finally:
            machine_token = None
            machine_pid = None
        if knocked.get("approved") is not True:
            raise LinkError("the brain did not approve the panel")
        token = self.store.get_part_token()
        if not token:
            raise LinkError("the brain approved the panel without a part key")
        self.auto_ap = False
        self.needs_provisioning = False
        self._set_link("brain")
        return token

    def knock(self, token: str, part: bool = False) -> dict:
        """Tell the brain the panel is here; return its answer.

        ``part=False`` is the one knock made with the machine key: its answer
        carries a part key, which is stored before the machine key goes out of
        scope. ``part=True`` is every knock after that and must carry the
        panel's own pid as ``key``, or the brain answers ``403``.
        """
        if token == self.store.get_part_token():
            part = True
        body = self.knock_body()
        try:
            status, answer = self._post("/api/bodies", token, body)
        except http.HttpError as error:
            self._refuse(error.status, "knock")
            raise

        if status == 401:
            self._requires_pairing()
            raise NeedsProvisioning("the brain no longer knows this panel")
        if status == 429:
            raise Throttled("the brain is throttling knocks")
        if status >= 400:
            raise LinkError(f"the brain refused the knock with status {status}",
                            status)

        named = answer.get("pid")
        if named is not None and named != self.pid:
            raise LinkError("the brain knows this panel by another name")
        if answer.get("approved") is not True:
            return answer

        self._remember_pid()
        if part:
            return answer

        issued = answer.get("token")
        if not isinstance(issued, str) or not issued:
            raise LinkError("the knock was approved without a part key")
        self.store.set_part_token(issued)
        return answer

    def knock_body(self) -> dict:
        """The exact JSON the brain expects, with nothing inferred on its side.

        No ``started``: it is an epoch float the panel does not have, and an
        absent one is honest. ``key`` and ``host`` are explicit because the
        brain otherwise derives a pid from its own hostname, which is not the
        panel's name across a network.
        """
        return {
            "v": PROTOCOL,
            "version": VERSION,
            "key": self.pid,
            "host": self.host,
            "channel": self.channel,
            "forms": ["output"],
            "about": ABOUT,
            "ui": [{"name": "now", "kind": "label", "label": "Screen",
                    "value": "BOOT", "head": True}],
            "hears": "all",
            # The BOOT button answers an open permission with yes; the brain
            # lets it only after the person let the panel in.
            "approves": True,
            # What the agent may do here; the brain asks the person first
            # for every pin.
            "acts": device.acts(),
        }

    def events(self, kinds: str = EVENT_KINDS) -> bool:
        """Open the status stream and fold every event into the display.

        Blocks until the stream ends (EOF, silence, or ``close``) and returns
        ``True`` when the head arrived. The caller knocks first; this method
        never opens a stream it has not been handed.
        """
        token = self.store.get_part_token()
        if not token:
            self._requires_pairing()
            raise NeedsProvisioning("no part key to listen with")

        sock = self._connect(self.silence_s)
        self._sock = sock
        parser = sse.SSEParser()
        try:
            try:
                http.open_stream(sock, "GET", "/api/events?kinds=" + kinds,
                                 self.brain_host, token, self.port)
            except http.HttpError as error:
                self._refuse(error.status, "stream")
                raise

            sock.settimeout(self.silence_s)
            self._set_link("online")
            while not self._closed:
                try:
                    chunk = sock.recv(READ)
                except OSError:
                    break
                if not chunk:
                    break
                for event in parser.feed(chunk):
                    self._apply(event, "online")
            return True
        finally:
            self._forget_socket()

    def supervise(self, stop=None) -> None:
        """Keep the panel connected: knock, listen, and come back.

        One connection at a time, and a knock before every stream. A missing
        or revoked key is terminal: the store is cleared, the display says
        ``PAIR``, ``auto_ap`` is raised for the caller, and the exception goes
        out rather than a loop of knocks. Everything else — a brain that is
        down, a stream that dies, a part still waiting for a person, a
        throttle — waits and tries again.
        """
        attempt = 0
        while not self._closed:
            if stop is not None and stop():
                return
            token = self.store.get_part_token()
            if not token:
                self._requires_pairing()
                raise NeedsProvisioning("no part key to knock with")

            try:
                answer = self.knock(token, part=True)
            except NeedsProvisioning:
                raise
            except Throttled as error:
                self._link_down()
                self._wait(error.retry_s + self._jitter())
                continue
            except (LinkError, OSError):
                self._link_down()
                attempt = self._failed(attempt)
                continue

            if answer.get("approved") is not True:
                self._not_approved(answer)
                attempt = self._failed(attempt)
                continue

            try:
                opened = self.events()
            except NeedsProvisioning:
                raise
            except Throttled as error:
                self._link_down()
                self._wait(error.retry_s + self._jitter())
                continue
            except (LinkError, OSError):
                self._link_down()
                attempt = self._failed(attempt)
                continue

            if opened:
                attempt = 0
            self._link_down()
            attempt = self._failed(attempt)

    def report(self, values: dict) -> bool:
        """Post the panel's state, but only when it differs from the last.

        The panel has one head label and nothing to say in words; a report is
        ``state`` values for its own channel, never a ``heard``/``said`` text.
        Returns whether a request was made.
        """
        if not isinstance(values, dict):
            raise LinkError("values must be a mapping")
        if self._last_values is not None and values == self._last_values:
            return False

        token = self.store.get_part_token()
        if not token:
            self._requires_pairing()
            raise NeedsProvisioning("no part key to report with")

        body = {
            "channel": self.channel,
            "kind": "state",
            "values": dict(values),
        }
        try:
            status, _answer = self._post("/api/input", token, body)
        except http.HttpError as error:
            if error.status == 401:
                self._requires_pairing()
                raise NeedsProvisioning("the brain no longer knows this panel") from error
            return False
        except OSError:
            return False

        if status == 401:
            self._requires_pairing()
            raise NeedsProvisioning("the brain no longer knows this panel")
        if status != 200:
            return False
        self._last_values = dict(values)
        return True

    def close(self) -> None:
        """Stop the loop and drop the open stream; safe to call twice."""
        self._closed = True
        self._forget_socket()

    def _connect(self, timeout: float | None = None):
        sock = self.socket_factory()
        try:
            sock.settimeout(CONNECT_S if timeout is None else timeout)
            sock.connect((self.brain_host, self.port))
        except Exception:
            sock.close()
            raise
        return sock

    def permit(self, permission_id: int) -> bool:
        """Say yes to the permission ``permission_id``; True if it was open.

        Only yes: the brain refuses a part's no, and a stale id is answered
        with ``answered: false`` instead of allowing something else.
        """
        token = self.store.get_part_token()
        if not token:
            return False
        try:
            status, answer = self._post(
                "/api/permission/" + str(int(permission_id)), token, {"allow": True}
            )
        except (http.HttpError, OSError, ValueError):
            return False
        return status == 200 and answer.get("answered") is True

    def _post(self, path: str, token: str, body: dict) -> tuple[int, dict]:
        sock = self._connect()
        try:
            return http.post_json(
                sock, "POST", path, self.brain_host, token, body, self.port)
        finally:
            sock.close()

    def _forget_socket(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    @property
    def state(self) -> str:
        """The one word the panel should draw right now."""
        return self.display.state

    def _apply(self, event: dict, link: str | None = None) -> None:
        if event.get("kind") == "control":
            self._control(event)
            return
        before = self.display.state
        self.display = state.reduce(self.display, event, self._now(), link)
        if self.display.state != before:
            self._notify()

    def _control(self, event: dict) -> None:
        # The panel hears the whole conversation, so it hears other parts'
        # controls too; only its own are acted on.
        if event.get("part") != self.pid or self.on_control is None:
            return
        try:
            self.on_control(event.get("name"), event.get("value"))
        except Exception:  # noqa: BLE001 - an act must not stop the stream
            return

    def _set_link(self, link: str) -> None:
        before = self.display.state
        self.display = state.reduce(self.display, {}, self._now(), link)
        if self.display.state != before:
            self._notify()

    def _link_down(self) -> None:
        self._set_link("offline" if self.display.was_online else "brain")

    def _notify(self) -> None:
        if self.on_state is None:
            return
        try:
            self.on_state(self.display.state)
        except Exception:  # noqa: BLE001 — a renderer must not stop the stream
            return

    def _requires_pairing(self) -> None:
        try:
            self.store.clear_part_token()
        except (AttributeError, OSError, ValueError):
            pass
        self.auto_ap = True
        self.needs_provisioning = True
        before = self.display.state
        self.display = state.initial("pair")
        if self.display.state != before:
            self._notify()
        self._forget_socket()

    def _remember_pid(self) -> None:
        if self.store.get_pid() != self.pid:
            self.store.set_pid(self.pid)

    def _not_approved(self, answer: dict) -> None:
        why = str(answer.get("why") or "")
        if "reject" in why or "expired" in why or "ніхто" in why:
            self._apply({"kind": "cannot"}, "pair")
        else:
            self._set_link("pair")

    def _failed(self, attempt: int) -> int:
        self._wait(self._delay(attempt))
        return attempt + 1

    def _delay(self, attempt: int) -> float:
        base = BACKOFF_S[min(attempt, len(BACKOFF_S) - 1)]
        return base + self._jitter()

    def _jitter(self) -> float:
        return (self._now() % 1000) / 1000.0

    def _wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        if not self._closed:
            time.sleep(seconds)

    def _refuse(self, status: int | None, what: str) -> None:
        if status == 401:
            self._requires_pairing()
            raise NeedsProvisioning("the brain no longer knows this panel")
        if status == 429:
            raise Throttled("the brain is throttling the panel")
        if status is None:
            raise LinkError(f"the brain's {what} could not be read")
        raise LinkError(f"the brain refused the {what} with status {status}", status)

    def _now(self) -> int:
        ticks = getattr(self.clock, "ticks_ms", None)
        if ticks is not None:
            try:
                return ticks()
            except AttributeError:
                pass
        now = getattr(self.clock, "now_ms", None)
        if now is not None:
            try:
                return now()
            except (AttributeError, TypeError):
                pass
        try:
            return int(time.monotonic() * 1000) % (1 << 30)
        except AttributeError:
            return int(time.time() * 1000) % (1 << 30)

    @staticmethod
    def _identity(config: dict, store) -> tuple[str, str, str]:
        """The stable names: channel, host, pid. Never a user-entered name.

        After the first pairing the pid is the source of truth. Before that
        the portal has already written the identity host into
        ``device.name``; a bare MAC or board tag is accepted as a convenience.
        """
        stored = store.get_pid()
        if isinstance(stored, str) and "@" in stored:
            channel, host = stored.split("@", 1)
            return channel, host, channel + "@" + host

        name = str((config.get("device") or {}).get("name") or "").strip()
        if name.startswith("esp32c3-"):
            tag = name[len("esp32c3-"):]
        elif name:
            compact = name.replace(":", "").lower()
            if len(compact) >= 4 and all(ch in "0123456789abcdef" for ch in compact):
                tag = compact[-4:] if len(compact) > 8 else compact[:4]
            else:
                tag = name
        else:
            tag = "0000"
        host = "esp32c3-" + tag
        channel = "oled-" + tag
        return channel, host, channel + "@" + host

    def __repr__(self) -> str:
        return f"Link(pid={self.pid!r}, brain={self.brain_host}:{self.port})"
