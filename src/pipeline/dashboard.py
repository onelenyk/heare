"""Read the run journal, render a page you can actually look at.

`room-runs.jsonl` grows a line per scenario per run. A line is enough to
decide whether the last run passed; it is useless for the question that
matters more — is anything drifting? Barge-in was 503 ms before the VAD
thresholds went up and 899 ms after. Both inside budget. Nobody would
have noticed the second number without putting them side by side.

    uv run python -m src.pipeline.dashboard
    open ~/.heare/room-dashboard.html

Self-contained: no scripts, no fonts, no requests. The charts are SVG
generated here rather than drawn by a library in the browser, so the file
means the same thing in a year.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

JOURNAL = Path.home() / ".heare" / "room-runs.jsonl"
OUTPUT = Path.home() / ".heare" / "room-dashboard.html"

# Order matters: this is the order the scenarios are meant to be read in,
# from "does it speak at all" to "does it stop when told".
ORDER = ["hello", "addressed", "unaddressed", "interrupt", "delegate", "stop"]

WHAT_IT_ASKS = {
    "hello": "Чи відповідає, і чи швидко",
    "addressed": "Мовчить, доки не покличуть імʼям",
    "unaddressed": "Чує кімнату — і не відповідає їй",
    "interrupt": "Замовкає, коли заговорити поверх",
    "delegate": "Каже «гляну», потім справжню відповідь",
    "stop": "«Стоп» гасить і голос, і роботу",
}


@dataclass
class Run:
    ts: float
    rows: list[dict]

    @property
    def passed(self) -> int:
        return sum(1 for r in self.rows if not r.get("failures"))

    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def ok(self) -> bool:
        return self.passed == self.total and self.total > 0


def load(journal: Path) -> list[Run]:
    """Group the journal into runs.

    Lines written within a few minutes of each other are one run — the
    suite writes them as it goes, and nothing else writes to this file.
    """
    if not journal.exists():
        return []

    rows: list[dict] = []
    for line in journal.read_text("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    runs: list[Run] = []
    for row in rows:
        ts = float(row.get("ts") or 0.0)
        if runs and (ts == 0.0 or ts - runs[-1].ts < 600):
            runs[-1].rows.append(row)
            runs[-1].ts = max(runs[-1].ts, ts)
        else:
            runs.append(Run(ts=ts, rows=[row]))
    return runs


# ── drawing ───────────────────────────────────────────────────────────


def sparkline(values: list[float | None], width: int = 132, height: int = 34) -> str:
    """A run-over-run trace with the last point called out.

    Gaps are real: a scenario that did not fire barge-in has no number,
    and joining across that would draw a line through something that
    never happened.
    """
    points = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(points) < 2:
        return (
            f'<svg class="spark" viewBox="0 0 {width} {height}" '
            f'role="img" aria-label="недостатньо прогонів"></svg>'
        )

    ys = [v for _, v in points]
    lo, hi = min(ys), max(ys)
    span = (hi - lo) or max(hi, 1.0)
    pad = 4

    def px(i: int) -> float:
        return pad + i * (width - 2 * pad) / max(len(values) - 1, 1)

    def py(v: float) -> float:
        return height - pad - (v - lo) / span * (height - 2 * pad)

    line = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in points)
    area = f"{px(points[0][0]):.1f},{height} {line} {px(points[-1][0]):.1f},{height}"
    lx, lv = points[-1]
    return (
        f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{len(points)} прогонів">'
        f'<polygon class="spark-fill" points="{area}"/>'
        f'<polyline class="spark-line" points="{line}"/>'
        f'<circle class="spark-dot" cx="{px(lx):.1f}" cy="{py(lv):.1f}" r="2.6"/>'
        f"</svg>"
    )


def ms(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}".replace(",", " ")


def when(ts: float) -> str:
    if not ts:
        return "невідомо коли"
    delta = time.time() - ts
    if delta < 90:
        return "щойно"
    if delta < 3600:
        return f"{int(delta // 60)} хв тому"
    if delta < 86400:
        return f"{int(delta // 3600)} год тому"
    return time.strftime("%d.%m %H:%M", time.localtime(ts))


# ── the page ──────────────────────────────────────────────────────────

CSS = """
:root{
  --ground:#f5f7f9; --surface:#ffffff; --sunk:#eef1f5;
  --ink:#131820; --muted:#5c6675; --rule:#e0e5ec;
  --voice:#b06a12;      /* the person speaking */
  --assistant:#0f6f70;  /* the assistant speaking */
  --pass:#1a6a46; --fail:#a3311b; --pass-wash:#e6f2eb; --fail-wash:#fbeae6;
  --shadow:0 1px 2px rgb(19 24 32 / .06), 0 8px 20px -12px rgb(19 24 32 / .18);
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --ground:#11141a; --surface:#181c23; --sunk:#141820;
    --ink:#e7eaf0; --muted:#939daf; --rule:#262c37;
    --voice:#dd9d51; --assistant:#4fb2b3;
    --pass:#5ec08c; --fail:#e8765c; --pass-wash:#16281f; --fail-wash:#2a1a16;
    --shadow:0 1px 2px rgb(0 0 0 / .4), 0 10px 24px -14px rgb(0 0 0 / .8);
  }
}
:root[data-theme="dark"]{
  --ground:#11141a; --surface:#181c23; --sunk:#141820;
  --ink:#e7eaf0; --muted:#939daf; --rule:#262c37;
  --voice:#dd9d51; --assistant:#4fb2b3;
  --pass:#5ec08c; --fail:#e8765c; --pass-wash:#16281f; --fail-wash:#2a1a16;
  --shadow:0 1px 2px rgb(0 0 0 / .4), 0 10px 24px -14px rgb(0 0 0 / .8);
}
*{box-sizing:border-box}
body{
  margin:0; background:var(--ground); color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1080px;margin:0 auto;padding:40px 24px 72px;
  display:flex;flex-direction:column;gap:32px}
.mono{font-family:ui-monospace,SFMono-Regular,"SF Mono",Menlo,monospace;
  font-variant-numeric:tabular-nums}

header{display:flex;flex-wrap:wrap;align-items:baseline;gap:12px 20px}
h1{margin:0;font-size:23px;letter-spacing:-.015em;font-weight:650;
  text-wrap:balance}
.sub{color:var(--muted);font-size:14px}
.verdict{margin-left:auto;display:flex;align-items:center;gap:10px;
  padding:7px 14px;border-radius:999px;font-weight:650;font-size:14px}
.verdict.ok{background:var(--pass-wash);color:var(--pass)}
.verdict.bad{background:var(--fail-wash);color:var(--fail)}

.grid{display:grid;gap:14px;
  grid-template-columns:repeat(auto-fill,minmax(320px,1fr))}
.card{background:var(--surface);border:1px solid var(--rule);border-radius:12px;
  box-shadow:var(--shadow);overflow:hidden;display:flex;flex-direction:column}
.card .stripe{height:3px}
.card.ok .stripe{background:var(--pass)}
.card.bad .stripe{background:var(--fail)}
.card .body{padding:16px 18px;display:flex;flex-direction:column;gap:12px;flex:1}
.name{display:flex;align-items:baseline;gap:9px}
.name b{font-size:16px;letter-spacing:-.01em}
.tag{font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  padding:2px 7px;border-radius:5px;font-weight:650}
.tag.ok{background:var(--pass-wash);color:var(--pass)}
.tag.bad{background:var(--fail-wash);color:var(--fail)}
.asks{color:var(--muted);font-size:13.5px;margin:-4px 0 0}

.metrics{display:flex;gap:18px;flex-wrap:wrap;margin-top:auto}
.metric{display:flex;flex-direction:column;gap:1px}
.metric .k{font-size:11px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted)}
.metric .v{font-size:17px;font-weight:600}
.metric .v small{font-size:11px;color:var(--muted);font-weight:400;
  margin-left:2px}

.why{background:var(--fail-wash);color:var(--fail);border-radius:8px;
  padding:9px 11px;font-size:13px;display:flex;flex-direction:column;gap:3px}

.track{display:flex;height:22px;border-radius:5px;overflow:hidden;
  background:var(--sunk);border:1px solid var(--rule)}
.track i{display:block;height:100%}
.track i.said{background:var(--voice)}
.track i.spoke{background:var(--assistant)}
.legend{display:flex;gap:14px;font-size:11.5px;color:var(--muted);
  align-items:center;flex-wrap:wrap}
.legend span{display:flex;align-items:center;gap:5px}
.dot{width:8px;height:8px;border-radius:2px;display:inline-block}
.dot.said{background:var(--voice)} .dot.spoke{background:var(--assistant)}

section h2{font-size:12px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);margin:0 0 12px;font-weight:650}
table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;
  color:var(--muted);font-weight:600;padding:0 10px 8px 0}
td{padding:9px 10px 9px 0;border-top:1px solid var(--rule);vertical-align:middle}
td.num{text-align:right;padding-right:16px}
.scroll{overflow-x:auto}
.spark{width:132px;height:34px;display:block}
.spark-line{fill:none;stroke:var(--assistant);stroke-width:1.6;
  stroke-linejoin:round;stroke-linecap:round}
.spark-fill{fill:var(--assistant);opacity:.12}
.spark-dot{fill:var(--assistant)}
.note{color:var(--muted);font-size:13px;max-width:66ch}
.empty{background:var(--surface);border:1px dashed var(--rule);border-radius:12px;
  padding:28px;color:var(--muted)}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;
  background:var(--sunk);padding:1.5px 5px;border-radius:4px}
"""


def _track(row: dict) -> str:
    """A single bar: when the person spoke, when the assistant did.

    Reading a scenario is mostly reading its shape — an acknowledgement
    followed by a gap followed by an answer looks different from two
    acknowledgements, and the difference is the bug that shipped.
    """
    spoken = row.get("utterances") or 0
    first = row.get("first_reply_ms")
    if not spoken and not first:
        return ""
    parts = ['<div class="track">']
    parts.append('<i class="said" style="width:22%"></i>')
    gap = min(46, max(8, (first or 3000) / 260))
    parts.append(f'<i style="width:{gap:.0f}%"></i>')
    each = max(6, (100 - 22 - gap) / max(spoken, 1))
    for _ in range(min(spoken, 4)):
        parts.append(f'<i class="spoke" style="width:{each:.0f}%"></i>')
    parts.append("</div>")
    return "".join(parts)


def render(runs: list[Run]) -> str:
    if not runs:
        body = (
            '<div class="empty">Журнал порожній. Запусти <code>make e2e</code> — '
            "після першого прогону тут буде що показати.</div>"
        )
        return _page(body, "жодного прогону")

    last = runs[-1]
    by_name = {r["scenario"]: r for r in last.rows}
    ordered = [n for n in ORDER if n in by_name] + [
        n for n in by_name if n not in ORDER
    ]

    cards = []
    for name in ordered:
        row = by_name[name]
        failures = row.get("failures") or []
        state = "bad" if failures else "ok"
        why = ""
        if failures:
            why = '<div class="why">' + "".join(
                f"<div>{html.escape(str(f))}</div>" for f in failures
            ) + "</div>"

        metrics = []
        first = row.get("first_reply_ms")
        if first is not None:
            metrics.append(
                f'<div class="metric"><span class="k">перша відповідь</span>'
                f'<span class="v mono">{ms(first)}<small>мс</small></span></div>'
            )
        if row.get("barge_in_ms") is not None:
            metrics.append(
                f'<div class="metric"><span class="k">перебивання</span>'
                f'<span class="v mono">{ms(row["barge_in_ms"])}'
                f"<small>мс</small></span></div>"
            )
        metrics.append(
            f'<div class="metric"><span class="k">реплік</span>'
            f'<span class="v mono">{row.get("utterances", 0)}</span></div>'
        )
        metrics.append(
            f'<div class="metric"><span class="k">почув себе</span>'
            f'<span class="v mono">{row.get("heard_itself", 0)}</span></div>'
        )

        cards.append(
            f'<article class="card {state}"><div class="stripe"></div>'
            f'<div class="body">'
            f'<div class="name"><b>{html.escape(name)}</b>'
            f'<span class="tag {state}">{"впав" if failures else "ок"}</span></div>'
            f'<p class="asks">{html.escape(WHAT_IT_ASKS.get(name, ""))}</p>'
            f"{why}{_track(row)}"
            f'<div class="metrics">{"".join(metrics)}</div>'
            f"</div></article>"
        )

    trends = _trends(runs, ordered)

    verdict_class = "ok" if last.ok else "bad"
    verdict = (
        f"{last.passed}/{last.total} пройшло"
        if last.ok
        else f"{last.total - last.passed} з {last.total} впало"
    )

    body = f"""
<header>
  <h1>Кімната</h1>
  <span class="sub">наскрізні прогони демона · {html.escape(when(last.ts))}</span>
  <span class="verdict {verdict_class}">{verdict}</span>
</header>

<div class="legend">
  <span><i class="dot said"></i> говорить людина</span>
  <span><i class="dot spoke"></i> говорить асистент</span>
  <span>смуга показує форму розмови, не точний масштаб</span>
</div>

<div class="grid">{"".join(cards)}</div>

{trends}

<p class="note">Кожен сценарій ганяє зібраний демон через симульовану
кімнату: синтезована мова, підмішане ехо, справжні розпізнавання й модель.
Оновити — <code>make e2e</code>, потім
<code>uv run python -m src.pipeline.dashboard</code>.</p>
"""
    return _page(body, f"{last.passed}/{last.total}")


def _trends(runs: list[Run], names: list[str]) -> str:
    if len(runs) < 2:
        return (
            '<section><h2>Динаміка</h2><p class="note">Потрібен щонайменше '
            "другий прогін, щоб було з чим порівнювати. Саме тут видно "
            "повільне сповзання — таке, що досі вкладається в межу.</p></section>"
        )

    rows = []
    for name in names:
        series = [
            next((r for r in run.rows if r["scenario"] == name), None) for run in runs
        ]
        first = [None if s is None else s.get("first_reply_ms") for s in series]
        barge = [None if s is None else s.get("barge_in_ms") for s in series]
        fails = sum(1 for s in series if s and s.get("failures"))
        rows.append(
            f"<tr><td>{html.escape(name)}</td>"
            f"<td>{sparkline(first)}</td>"
            f'<td class="num mono">{ms(first[-1])}</td>'
            f"<td>{sparkline(barge)}</td>"
            f'<td class="num mono">{ms(barge[-1])}</td>'
            f'<td class="num mono">{fails}/{len(runs)}</td></tr>'
        )

    return f"""
<section><h2>Динаміка за {len(runs)} прогонів</h2>
<div class="scroll"><table>
<thead><tr><th>сценарій</th><th>перша відповідь</th><th class="num">мс</th>
<th>перебивання</th><th class="num">мс</th><th class="num">падінь</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div></section>
"""


def _page(body: str, title_bit: str) -> str:
    return (
        f"<title>Кімната — {html.escape(title_bit)}</title>\n"
        f"<style>{CSS}</style>\n"
        f'<div class="wrap">{body}</div>\n'
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--journal", type=Path, default=JOURNAL)
    p.add_argument("--out", type=Path, default=OUTPUT)
    args = p.parse_args()

    runs = load(args.journal)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(runs), encoding="utf-8")

    scenarios = sum(r.total for r in runs)
    print(f"{args.out}  —  {len(runs)} прогонів, {scenarios} сценаріїв")
    if runs:
        last = runs[-1]
        print(f"останній: {last.passed}/{last.total} пройшло, {when(last.ts)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
