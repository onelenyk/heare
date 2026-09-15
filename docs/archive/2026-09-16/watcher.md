# Watcher: a role whose input is not the conversation

Design note, 2026-08-20. Every capability claim below was checked by
reading the code or running the command on this machine; where something
was not verified, it says so.

## What it is

The four roles that exist — мітинг, інтерв'ю, вчитель, суфлер — all live
inside a turn. You speak, it answers. Even суфлер, which talks aside
rather than into the conversation, is reacting to something it heard.

Watcher reacts to what was **never said to anyone**. It observes the
environment and decides, on its own, whether anything is worth mentioning.

That makes it the first role that needs the engine built on 2026-08-18.
`judge()` already decides *when* it may speak — not mid-turn, not at
night, not to an empty room, not too soon after last time, and less often
each time it is brushed off. What the engine has never had is anything
worth speaking *about*: its three event sources (a job finished, a job is
slow, an MCP server died) are all the assistant reporting on itself.

Watcher is the sensor half of the thing we already built.

## The environment, as axes

**Hears** — the microphone (works, continuously) and **what comes out of
the speakers** (does not exist). The distinction matters: the first is
you, the second is everyone else in the call.

**Sees** — front application, window titles, screen pixels, the active
browser tab and its text, the clipboard, files that just changed.

**Does** — already there: bash, files, browser drive, delegation.

**Rhythm** — how long you have been on this, how often you switch, when
you last changed anything. Probably the single most useful thing a
watcher can say, and it needs no new permission at all.

**Presence** — whether you are at the keyboard. `HIDIdleTime` is free and
removes half the false positives.

**Context that explains the rest** — calendar (a meeting explains why it
suddenly got loud, and why now is not the moment), battery, network, a
long build running.

The unit is not a state but a **change**. A watcher that reports "Chrome
is open" is noise; one that notices "you switched away from the thing you
had been on for three hours" is not.

## What is reachable, measured

Verified on this machine, 2026-08-19/20.

### Free today — no install, no permission

| | how |
|---|---|
| front application + bundle id | `NSWorkspace.frontmostApplication()` — pyobjc is already installed, `rumps` brought it for the menu bar |
| list of apps with windows | `NSWorkspace.runningApplications()`, filtered by activation policy |
| idle time | `ioreg -c IOHIDSystem` → `HIDIdleTime`, microsecond resolution |
| clipboard | `pbpaste` answers (checked by byte count, content not read) |
| power, sleep settings | `pmset -g`, `pmset -g batt` |
| processes | `ps` |

### One line of code away

The Chrome bridge. The extension is finished and the daemon side is
finished — 663 lines with token auth and pair codes. It supports
`list_tabs` (id, url, title, active — for every tab), `read_page`
(`document.body.innerText`, capped at 50 000 chars), `extract` (up to 100
nodes by CSS selector), plus click, fill, navigate.

It is dead: `BrowserBridge(...)` is constructed nowhere in `src/` and
`set_bridge()` is called nowhere. All eight browser tools are still
advertised to the worker and all eight answer "not connected"
(`src/agent/tools/direct.py:3388`). `src/api.py:576` already says so in a
comment.

Reconnecting is `await bridge.start()` + `set_bridge(bridge)` inside
`run_spine_engine` (`src/daemon/spine_engine.py`). It is the cheapest
capability in the whole inventory and the difference between a watcher
that knows what you are reading and one that knows nothing.

### One install away

* **Window titles and the accessibility tree** — `pyobjc-framework-quartz`
  plus the Accessibility grant. `Quartz`, `ApplicationServices` and
  `HIServices` do not currently import.
* **System audio** — there is no loopback device on this machine; the
  audio stack is exactly `Мікрофон MacBook Pro` and `Динаміки MacBook
  Pro`. BlackHole or an aggregate device is required.

### A subsystem away

* **Screen capture** — `screencapture` exists but nothing in `src/` has
  ever produced a pixel. Needs the Screen Recording grant.
* **Vision in the model** — `src/spine/llm.py` has no image support, and
  `_to_anthropic` coerces content with `str()` (`llm.py:222,226`), so an
  image block would be silently turned into garbage. Sending a screenshot
  needs both a capture path and a rewrite of request building.

## Read the text, not the pixels

The important design choice. OCR reads what a screen *looks like*; the
DOM and the accessibility tree read what an application *means*. A
heading stays a heading, a message from the other person stays separate
from yours, a link carries its URL rather than an underline. Vision
cannot reliably recover that, and costs ~5 900 tokens per frame against
approximately zero.

Pixels are still the honest answer for applications that draw rather than
lay out text: charts, video, canvas, a shared screen inside Meet. That is
a narrow case, and it is exactly the case for an eye that opens on an
event rather than on a timer.

## Cheap senses always, expensive senses on a trigger

The same shape that already works in `judge`: everything that can be a
condition is a condition, and the model is asked one question only after
the conditions have let something through.

```
every few seconds, free      front app, window title, idle, clipboard
                             change, changed files
        │  something changed
        ▼
on the event, near-free      accessibility tree or DOM of the active
                             window
        │  is there anything here worth saying?
        ▼
the model, rarely            one question — the `ask` hook
        │  yes
        ▼
engine.notice(...)           and judge() decides *when*
```

A screenshot every 30 seconds is ~700 000 input tokens an hour — dollars
per hour and a latency that rules out real time. A screenshot at the
moment you switch to something new after an hour on one thing is a few
per day, and lands.

## Two things that must be built before, not after

**The model's veto is not wired.** `Engine(...)` is constructed at
`src/spine/main.py:328` without `ask=`, so every intent that clears
`judge` is read out verbatim as stored. That is fine for "the disk check
finished". It is not fine for a watcher, whose intents are guesses about
what you are doing. `ask` (`src/spine/engine.py:288`) is the seam; it
must be connected in the same change that gives the watcher its first
sensor.

**A watcher without forgetting is surveillance.** The difference is not
intent, it is whether "what do you see right now?" and "what will you
still know in an hour?" have answers. Concretely: a list of what is never
looked at (banking, password managers, private windows), and a lifetime
on everything collected. This is part of what the role *is*, not a
setting added later.

Related, and easy to miss: **inside a role session the wake gate is
bypassed** (`src/spine/loop.py:276`) — the whole room reaches the model
without anyone saying a name. Watcher inherits that automatically. It is
what you want and it is the reason privacy comes first.

## The hard problem, which is not technical

In мітинг, the role defines what is worth saying. In watcher, nothing
does. Ninety-nine percent of what it sees deserves no words at all, and a
watcher that comments on every window switch gets turned off within the
hour.

So the target behaviour is not "helpful commentary". It is being able to
stay silent for a day and then say one thing that was worth the day. No
test can decide whether that has been achieved.

## Order of work

1. **Cheap senses, today.** Front app, window list, idle, clipboard,
   power — sampled every few seconds, diffed into events. No permission,
   no cost. Enough for "you have been in one file for three hours" and
   "you are back after an hour away".
2. **Reconnect the browser bridge.** Two lines in the composition root;
   adds the active tab and its text.
3. **Wire `ask`, and give `Situation` environment fields.** Without both,
   the watcher either says nothing or chatters.
4. **The accessibility tree**, for applications other than the browser.
5. **Vision**, only for the narrow drawing case, and only after 3.

Hearing a Teams call is a separate project, and not mainly a code one: it
needs a loopback device, a second input stream, and a decision about the
echo canceller — which now works at 40–50 dB and is therefore actively
deleting exactly the voices we would want to hear.

## Related

* [findings/two-agents.md](findings/two-agents.md) — the voice/hands split
  a watcher's observations would run through.
* [findings/known-broken.md](findings/known-broken.md) — where the browser
  bridge and its silence have been tracked.
* `src/spine/engine.py` — `judge`, the gate that already exists.
* `roles/README.md` — how a role is defined as markdown.
