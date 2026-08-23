"""Build docs/stend.html — the testing bench, read off the code itself.

A page in a repository that lists what the system does is a page that
disagrees with the system within a week. This project has spent days on
exactly that shape: a switch nobody reads, a marker pointing at an empty
directory, a docstring describing behaviour that was removed. Writing
another one by hand would be repeating the mistake in a nicer font.

So the page is generated. The feature table, the numbers that decide the
behaviour, and the scenario list are read from the modules and from
`tests/e2e` at build time. If a switch is added, a constant changes or a
scenario is written, the page says so the next time it is built — and if
one is deleted, the page stops claiming it exists.

What is *not* generated is the judgement: the traps, and the list of
things nobody has measured. Those are written here, in prose, because no
amount of reading the source produces them.

    make stend        # rebuild docs/stend.html
"""

from __future__ import annotations

import ast
import html
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "stend.html"


# ── read the system ───────────────────────────────────────────────────


def switches() -> list[tuple[str, bool, str]]:
    sys.path.insert(0, str(ROOT))
    from src.spine.features import FEATURES

    return [(f.name, f.default, f.cost) for f in FEATURES]


def numbers() -> list[tuple[str, str, str]]:
    """The constants that decide behaviour, with why each is what it is.

    The values come from the modules; the reasons are here, because a
    number without its reason is a number somebody will «tune».
    """
    sys.path.insert(0, str(ROOT))
    import src.spine.engine as engine
    import src.spine.environment as environment
    import src.spine.hearing as hearing
    import src.spine.search as search
    import src.spine.situation as situation
    import src.spine.summary as summary

    return [
        ("Двигун тікає", f"{engine.TICK_S:.0f} с",
         "Досить часто, щоб дозрілий намір не чекав"),
        ("Пауза між непроханими", f"{engine.BASE_QUIET_S / 60:.0f} хв × довіра",
         f"Довіра від {engine.TRUST_MIN} до {engine.TRUST_MAX}: відшили — "
         "і пауза росте до двох годин"),
        ("Ніч", f"{situation.NIGHT_FROM}:00 – {situation.NIGHT_UNTIL:02d}:00",
         "Уночі проходить лише те, що ти просив, і лише термінове"),
        ("Присутність: клавіатура", f"{situation.AT_KEYBOARD_S / 60:.0f} хв",
         "Без цього мовчазна робота читалась як порожня кімната"),
        ("Кінець розмови", f"{engine.CONVERSATION_IDLE_S / 60:.0f} хв тиші",
         "У голосового асистента немає «покласти слухавку»"),
        ("Мінімум для підсумку", f"{summary.ENOUGH_LINES} рядки",
         "Менше — і модель напише речення замість зізнатись, що нічого не було"),
        ("Підслухане живе", f"{engine.OVERHEARD_KEEP_S / 86400:.0f} дн",
         "Різниця між робочою памʼяттю і записом, про який забули"),
        ("Пошук не бачить свіжіше", f"{search.NOT_YET_A_MEMORY_S:.0f} с",
         "Питання лягає в базу до виклику інструмента й інакше стає власною "
         "відповіддю"),
        ("Оглухнення", f"{hearing.SILENT_AFTER_S:.0f} с",
         "Кадри йдуть кожні десятки мілісекунд — це на три порядки більше"),
        ("watcher: осів у застосунку", f"{environment.SETTLED_S / 60:.0f} хв",
         "Менше — це перехід, а не «ти був у ньому»"),
        ("watcher: глибоко в одному", f"{environment.DEEP_S / 60:.0f} хв",
         "Раз на захід, і лише якщо ти був за столом увесь час"),
    ]


def scenarios() -> list[tuple[str, str, list[tuple[str, str]]]]:
    """Every e2e case, with the first line of what it says it is about."""
    out = []
    for path in sorted((ROOT / "tests" / "e2e").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        blurb = (ast.get_docstring(tree) or "").strip().split("\n")[0]
        cases = []
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name.startswith("test_"):
                doc = (ast.get_docstring(node) or "").strip().split("\n")[0]
                cases.append((node.name.removeprefix("test_").replace("_", " "), doc))
        out.append((path.name, blurb, cases))
    return out


def counted() -> tuple[str, str]:
    """How many tests there are, asked of pytest rather than guessed."""
    def collect(args: list[str]) -> str:
        try:
            done = subprocess.run(
                [sys.executable, "-m", "pytest", *args, "--collect-only", "-q"],
                cwd=ROOT, capture_output=True, text=True, timeout=180,
            )
            for line in reversed(done.stdout.strip().splitlines()):
                if "test" in line and "collected" in line:
                    return line.split("/")[0].strip().split(" ")[0]
        except Exception:  # noqa: BLE001
            pass
        return "—"

    return collect(["tests/", "--ignore=tests/integration"]), collect(["tests/e2e"])


# ── write the page ────────────────────────────────────────────────────


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def build() -> str:
    total, e2e = counted()
    rows = "\n".join(
        f'          <tr><td>{esc(n)}</td><td class="num">{esc(v)}</td>'
        f'<td class="what">{esc(w)}</td></tr>'
        for n, v, w in numbers()
    )
    chips = "\n".join(
        f'      <span class="chip {"on" if on else "off"}">{esc(name)}'
        f'<span class="st">{"увімк" if on else "вимк"}</span></span>'
        for name, on, _cost in switches()
    )
    off_costs = "\n".join(
        f"        <li><b>{esc(name)}</b> — {esc(cost)}</li>"
        for name, on, cost in switches() if not on
    )
    files = []
    for filename, blurb, cases in scenarios():
        items = "\n".join(
            f'          <li><b>{esc(name)}</b>'
            + (f' — {esc(doc)}' if doc else "")
            + "</li>"
            for name, doc in cases
        )
        files.append(
            f'      <div class="file">\n'
            f'        <h3>{esc(filename)} <span class="n">{len(cases)}</span></h3>\n'
            f'        <p>{esc(blurb)}</p>\n'
            f'        <ul>\n{items}\n        </ul>\n'
            f'      </div>'
        )
    return TEMPLATE.format(
        built=date.today().isoformat(),
        total=total,
        e2e=e2e,
        chips=chips,
        off_costs=off_costs,
        numbers=rows,
        files="\n".join(files),
    )


TEMPLATE = """<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Стенд heare</title>
<style>
  :root {{
    --ground:#F2F5F5; --surface:#FFF; --sunk:#E9EEEE;
    --ink:#14201F; --ink-soft:#4A5A59; --ink-faint:#758584;
    --rule:#D6DEDD; --rule-soft:#E4EAEA;
    --accent:#0B6A68; --accent-soft:#DCECEB;
    --warn:#7A5C1E; --warn-bg:#F2E9D4; --alarm:#A63A28;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --ground:#0E1414; --surface:#151D1D; --sunk:#101717;
      --ink:#E4EDEC; --ink-soft:#A3B3B2; --ink-faint:#7A8B8A;
      --rule:#26312F; --rule-soft:#1D2726;
      --accent:#4FBFB8; --accent-soft:#16302F;
      --warn:#D9BC75; --warn-bg:#2D2617; --alarm:#E28C7A;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    background: var(--ground); color: var(--ink); margin: 0;
    padding: 0 1.25rem 5rem; line-height: 1.6;
    font: 16px/1.6 "IBM Plex Sans", ui-sans-serif, system-ui, sans-serif;
  }}
  .wrap {{ max-width: 62rem; margin: 0 auto; }}
  h1, h2, h3 {{
    font-family: "IBM Plex Sans Condensed", "IBM Plex Sans", sans-serif;
    margin: 0; line-height: 1.15; text-wrap: balance;
  }}
  code, .code, .num, pre {{
    font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, monospace;
  }}
  header {{
    border-bottom: 2px solid var(--ink); padding: 3rem 0 1.4rem;
    display: flex; flex-direction: column; gap: .9rem;
  }}
  header h1 {{ font-size: clamp(2.2rem, 6vw, 3.4rem); font-weight: 600; }}
  .eyebrow {{
    font-size: .72rem; letter-spacing: .13em; text-transform: uppercase;
    color: var(--accent); margin: 0;
    font-family: "IBM Plex Mono", ui-monospace, monospace;
  }}
  .lede {{ color: var(--ink-soft); max-width: 44rem; margin: 0; }}
  .creed {{
    border-left: 3px solid var(--accent); background: var(--accent-soft);
    padding: .65rem 0 .65rem 1rem; max-width: 44rem; font-size: .95rem;
  }}
  section {{ padding: 2.6rem 0 0; display: flex; flex-direction: column; gap: 1rem; }}
  section > h2 {{ font-size: 1.45rem; font-weight: 600; }}
  section > p {{ margin: 0; color: var(--ink-soft); max-width: 44rem; }}
  .hint {{ font-size: .9rem; color: var(--ink-faint); max-width: 44rem; margin: 0; }}
  .counts {{ display: flex; flex-wrap: wrap; gap: 2rem; }}
  .count b {{ display: block; font-size: 2rem; font-weight: 600; line-height: 1.1;
              font-family: "IBM Plex Mono", monospace; }}
  .count span {{ font-size: .8rem; color: var(--ink-faint); letter-spacing: .06em;
                 text-transform: uppercase; }}
  .switches {{ display: flex; flex-wrap: wrap; gap: .45rem; }}
  .chip {{
    font-size: .8rem; padding: .28rem .6rem; border: 1px solid var(--rule);
    background: var(--surface); color: var(--ink-soft);
    display: inline-flex; gap: .45rem; align-items: baseline;
    font-family: "IBM Plex Mono", monospace;
  }}
  .chip.off {{ background: var(--warn-bg); border-color: var(--warn); color: var(--ink); }}
  .chip .st {{ font-size: .68rem; letter-spacing: .08em; color: var(--ink-faint); }}
  .chip.off .st {{ color: var(--warn); }}
  .scroller {{ overflow-x: auto; border: 1px solid var(--rule); background: var(--surface); }}
  table {{ border-collapse: collapse; width: 100%; font-size: .9rem; }}
  th, td {{ text-align: left; padding: .6rem .85rem;
            border-bottom: 1px solid var(--rule-soft); vertical-align: top; }}
  thead th {{
    font-size: .7rem; letter-spacing: .1em; text-transform: uppercase;
    color: var(--ink-faint); font-weight: 400; white-space: nowrap;
    border-bottom: 1px solid var(--rule);
    font-family: "IBM Plex Mono", monospace;
  }}
  tbody tr:last-child td {{ border-bottom: none; }}
  td.num {{ white-space: nowrap; font-variant-numeric: tabular-nums; }}
  td.what {{ color: var(--ink-soft); }}
  .files {{ display: flex; flex-direction: column; gap: 1px;
            background: var(--rule); border: 1px solid var(--rule); }}
  .file {{ background: var(--surface); padding: 1rem 1.15rem;
           display: flex; flex-direction: column; gap: .5rem; }}
  .file h3 {{ font-size: 1rem; font-weight: 600; display: flex; gap: .6rem;
              align-items: baseline; font-family: "IBM Plex Mono", monospace; }}
  .file h3 .n {{ font-size: .75rem; color: var(--ink-faint); font-weight: 400; }}
  .file > p {{ margin: 0; font-size: .9rem; color: var(--ink-soft); }}
  .file ul {{ margin: 0; padding-left: 1.1rem; display: flex;
              flex-direction: column; gap: .3rem; }}
  .file li {{ font-size: .88rem; color: var(--ink-soft); }}
  .file li b {{ color: var(--ink); font-weight: 500; }}
  .cards {{ display: grid; gap: 1px; background: var(--rule); border: 1px solid var(--rule); }}
  @media (min-width: 46rem) {{ .cards {{ grid-template-columns: 1fr 1fr; }} }}
  .card {{ background: var(--surface); padding: 1rem 1.15rem;
           display: flex; flex-direction: column; gap: .4rem; }}
  .card h3 {{ font-size: .98rem; font-weight: 600; color: var(--alarm); }}
  .card p {{ margin: 0; font-size: .9rem; color: var(--ink-soft); }}
  ul.plain {{ margin: 0; padding-left: 1.15rem; display: flex;
              flex-direction: column; gap: .5rem; }}
  ul.plain li {{ color: var(--ink-soft); font-size: .95rem; }}
  ul.plain li b {{ color: var(--ink); font-weight: 500; }}
  pre {{ background: var(--sunk); border: 1px solid var(--rule-soft);
         padding: .85rem .95rem; margin: 0; overflow-x: auto;
         font-size: .8rem; line-height: 1.55; }}
  footer {{
    margin-top: 3rem; padding-top: 1.1rem; border-top: 1px solid var(--rule);
    font-size: .76rem; color: var(--ink-faint);
    display: flex; flex-wrap: wrap; gap: .4rem 1.5rem;
    font-family: "IBM Plex Mono", monospace;
  }}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <p class="eyebrow">Голосовий асистент · власний хребет</p>
    <h1>Стенд heare</h1>
    <p class="lede">
      Що можна спостерігати, за якими числами воно поводиться, які
      сценарії вже ганяються самі — і чого ніхто не міряв.
    </p>
    <p class="creed">
      <strong>Правило, куплене дорого:</strong> фіча не рахується зробленою,
      поки не відпрацювала в живій розмові. Три дні роботи пройшли ревʼю,
      півтори тисячі тестів і не пройшли першої розмови.
    </p>
  </header>

  <section>
    <h2>Скільки</h2>
    <div class="counts">
      <div class="count"><b>{total}</b><span>усього тестів</span></div>
      <div class="count"><b>{e2e}</b><span>e2e-сценаріїв</span></div>
    </div>
    <p class="hint">
      Порахувала не ця сторінка, а pytest, під час збірки. Усе нижче
      прочитане з коду тоді ж: додай перемикач, зміни число, напиши
      сценарій — і сторінка про це скаже. Видали — і вона перестане
      стверджувати, що воно є.
    </p>
  </section>

  <section>
    <h2>З чого воно складається</h2>
    <p>
      Кожну підсистему можна вимкнути окремо. Вимкнена означає
      <em>не під'єднана взагалі</em>, а не «під'єднана й мовчить» — інакше
      вимикання нічого не доводить під час пошуку несправності.
    </p>
    <div class="switches">
{chips}
    </div>
    <p class="hint">Що втрачається, поки вимкнено:</p>
    <ul class="plain">
{off_costs}
    </ul>
  </section>

  <section>
    <h2>Три шари</h2>
    <p>
      Довго їх було два, і між ними діра — саме там жив кожен баг цього
      тижня. Юніти доводять правило й ніколи шлях; живий шар доводить
      усе, але повільно й за гроші.
    </p>
    <div class="scroller">
      <table>
        <thead><tr><th>Шар</th><th>Що доводить</th><th>Ціна</th></tr></thead>
        <tbody>
          <tr><td>Юніти</td>
              <td class="what">Правило. Кожен співрозмовник підроблений</td>
              <td class="num">~55 с</td></tr>
          <tr><td><b>e2e</b> · <span class="code">tests/e2e</span></td>
              <td class="what">Зібраний застосунок: справжній диригент, двигун,
                  база, інструменти. Підроблені лише вухо, рот і модель</td>
              <td class="num">~18 с</td></tr>
          <tr><td>Живий · <span class="code">spine_live</span></td>
              <td class="what">Справжні Groq і DeepSeek</td>
              <td class="num">гроші</td></tr>
        </tbody>
      </table>
    </div>
    <p class="hint">
      <span class="code">tests/e2e</span> була порожньою текою з маркером,
      що на неї вказує, і простояла так тижнями — рівно та форма, яку тут
      виловлювали весь тиждень: оголошено, ніхто не читає.
    </p>
  </section>

  <section>
    <h2>Числа, що визначають поведінку</h2>
    <p>
      Майже кожен тест — це «зачекати рівно стільки й подивитись». Числа
      прочитані з модулів; причини написані поруч, бо число без причини
      хтось «підкрутить».
    </p>
    <div class="scroller">
      <table>
        <thead><tr><th>Що</th><th>Скільки</th><th>Чому саме так</th></tr></thead>
        <tbody>
{numbers}
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <h2>Сценарії</h2>
    <p>Прочитані з <span class="code">tests/e2e</span> під час збірки.</p>
    <div class="files">
{files}
    </div>
  </section>

  <section>
    <h2>Пастки</h2>
    <p>Кожна вже зробила один тест порожнім або червоним не з тієї причини.</p>
    <div class="cards">
      <div class="card">
        <h3>Перезапуску не досить для recall</h3>
        <p>У промті є блок останніх шести обмінів, який читається з бази й
        перезапуск переживає. Модель відповість із контексту, пошук навіть
        не викличеться — тест виглядатиме пройденим і буде порожнім.</p>
      </div>
      <div class="card">
        <h3>Лог не показує, що воно сказало</h3>
        <p>Рядок <span class="code">say:</span> пишеться лише для речень від
        моделі. Підтвердження від інструментів озвучуються без нього.
        Дивитись треба в <span class="code">transcripts</span>.</p>
      </div>
      <div class="card">
        <h3>До моделі ведуть чотири дороги</h3>
        <p>Лише дві йдуть через диригента. Підсумовувач, вето на «озватись
        першим» і прохід по повторах беруть власне посилання під час
        проводки — перший прогін стенда витяг 401 у тесті без мережі.</p>
      </div>
      <div class="card">
        <h3>Двигун, що говорить, — це ще один хід</h3>
        <p>Він озивається через ту саму чергу впорскування. Тест, який
        тікає й одразу щось каже, жене два ходи крізь одну чергу й читає
        відповідь не на те.</p>
      </div>
      <div class="card">
        <h3>Годинник ходу — з налаштувань</h3>
        <p><span class="code">hold_s=0</span> не чіпає
        <span class="code">continuation_hold_s</span>, який 2,6 с за
        замовчуванням і тримає кожну репліку стільки ж.</p>
      </div>
      <div class="card">
        <h3>Мут — не поломка</h3>
        <p>Пристрій продовжує кликати, поки мут викидає кадри. Позначка
        часу береться <em>до</em> перевірки на мут.</p>
      </div>
    </div>
  </section>

  <section>
    <h2>Що знайшов сам стенд</h2>
    <p>Двигун чув себе і зараховував це як твою відповідь.</p>
    <pre>до         після непроханої репліки: awaiting = None
           після «не зараз, помовч»: довіра = 1.0

після      після непроханої репліки: awaiting = Intent(job_done…)
           після «не зараз, помовч»: довіра = 1.8</pre>
    <p class="hint">
      У докстрінзі двигуна написано, що саме consequence робить
      «озиватись вільно» стерпним і що це єдина причина, чому «вільно» не
      стає «постійно» за добу. Ця consequence не бачила жодної реакції за
      весь час існування фічі.
    </p>
  </section>

  <section>
    <h2>Чого ми не знаємо</h2>
    <p>Характеристики, яких ніхто не міряв. Тут кожен рядок — майбутній тест.</p>
    <ul class="plain">
      <li><b>Затримка.</b> Скільки минає від кінця твоєї фрази до першого
          звуку відповіді. На цьому хребті не міряли жодного разу.</li>
      <li><b>Акустика в кімнаті.</b> Ехоподавлення дає 40–50 дБ на стенді.
          Як воно тримається з музикою чи другим голосом — невідомо.</li>
      <li><b>Дистанція.</b> Чи переживає добу, сон, зміну пристрою. Відомо
          лише, що не переживає сон.</li>
      <li><b>Помилки воротаря імені.</b> Скільки звернень пропущено і
          скільки чужих реплік прийнято за звернення.</li>
      <li><b>Ціна ходу.</b> Токени й гроші на репліку — лічильник є,
          звіту немає.</li>
      <li><b>Чи не набридає.</b> Головне питання про проактивність, і на
          нього відповідає лише тиждень життя поруч.</li>
    </ul>
  </section>

  <footer>
    <span>зібрано {built}</span>
    <span>make stend</span>
    <span>tests/e2e · docs/live-tests.md · docs/findings/known-broken.md</span>
  </footer>

</div>
</body>
</html>
"""


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(f"✅ {OUT.relative_to(ROOT)}")
