# Оболонка сторінки: чат, док, поле (page-shell)

Крок «page-shell» переїзду сторінки. Читати разом із `.sisyphus/ui/sketch/` (вигляд, не код),
`packages/heare-brain/heare/api/page.html`, `.../server.py`, `packages/heare-core/heare/words.py`,
`tests/heare/test_page.py`, `tests/heare/test_language_rule.py`.
Тут — оболонка, файли й порядок переїзду. Стрічка/артефакти, закріплене, шухляда, памʼять/деталі/налаштування
мають власні спеки; ця задає їм раму, ідентифікатори й межі.

## 1. Цільовий DOM

```
#app
├─ #top                    56px; border-bottom; z 5; #idbtn, .topright
│  ├─ #idbtn               #face.face, .who>#name, #phase>i.livedot+#phasetext, .chev  (відкриває #system)
│  └─ .topright            #topmic.statepill, #topsound.statepill, #lastact, #model.modelchip,
│                          #stop.iconbtn, #opensettings.iconbtn
├─ #main                   grid, minmax(0,1fr), padding 12px 14px 14px
│  ├─ #system              fixed шухляда (sketch #system): .syshead, .syssec>.seclabel, #machines,
│  │                       .bodycard>#carousel>.slide+.bodyfoot>.dots, .sysfoot
│  └─ #chat                панель: border 1px var(--line), radius 18, overflow hidden, container-type
│     ├─ #pin-dock         док (#rail у скетчі): .chip(.trail|.shelf), .chip-thumb,
│     │                    .chip-text>b+small, .chip>.x, .dock-empty, .dock-div
│     ├─ #feed             скролер: scroll-padding-block, overscroll-behavior:contain
│     │  └─ #flow          900px: .msg(.me|.wide)>, .msg>.meta, .msg>.body, .feed-note, #empty
│     └─ #composer         бульбашка: #chips, form#say(#attach,#hear,#filepick,#text,button.send)
└─ поза #app: #veil, #sheet.sheet(.wide|.mid)>.sheethead>#sheettitle+#sheetclose, #sheetbody,
   #pinpreview, #toast, #pairing; тимчасово #stage>#live (у #sheet, див. M2)
```

**Власність.** `#top` — face/фаза/модель/стоп/налаштування, стан мікрофона й звуку, остання дія.
`#chat` — лише рамка, `--wing`, `::before/::after` (скрим), `--dock-h/--comp-h`, `.msg.wide`.
`#pin-dock` — поки що тільки розмітка чипа; логіка закріпленого — інша спека. `#feed`/`#flow` — скрол, відступи,
порожній стан. `#composer` — вкладення, голос, текст. `#system`/`#sheet` — кадри; вміст — інші спеки.

**Ідентифікатори.** Не перейменовувати без правки JS у тому ж коміті: `name`, `bigname`, `face`, `bigface`,
`mface`, `stop`, `confirm`, `stop-yes`, `stop-no`, `sessionpick`, `sessionnew`, `sessionname`, `sessiondrop`,
`save-key`, `lang`, `backup-sound`, `mind-*`, `pairing`, `paircode`, `text`, `say`, `filepick`, `chips`.
Тести тримають рядки `$("name").textContent`, `$("bigname").textContent`, `id="mind-panel-*"`,
`#mind { width:100%; border-left:0; }` і текст `Controls`, `Devices`, `Connect a device` у `page.html`.
`#talk` стає `#flow`; `#talkcol`, `#talkbar`, `#talkdrawer`, `#talktoggle`, `#mbar`, `#left`, `#pult`, `#partcol` — зникають по кроках.

## 2. Токени й правила

Усе нове — на наявних `:root`: `--bg --card --card-2 --line --ink --soft --accent --accent-ink --ok --warn
--bad --info --display --sans --mono --s1..--s5 --r1..--r3 --meta --small`. Нові лише геометричні:

```css
#chat { --flow:900px; --flow-pad:30px; --dock-h:58px; --comp-h:62px; --wing:0px;
        container-type:inline-size; position:relative; }
@supports (container-type:inline-size) {
  #chat { --wing:max(0px, calc((100cqw - (var(--flow) + 2 * var(--flow-pad))) / 2 - 12px)); } }
#flow { box-sizing:content-box; max-width:var(--flow); margin-inline:auto;
        padding-inline:var(--flow-pad); display:flex; flex-direction:column; gap:25px; }
```

- **Крила.** `--wing` — вільне місце панелі обабіч колонки. `1cqw` = 1% ширини `#chat`, тому відсотки не
  плутаються з відсотками `#flow` (у `width` і `margin` вони мають різні бази). Широке повідомлення:
  `.msg.wide { width:calc(100% + 2 * (var(--wing) + var(--flow-pad)));
  margin-inline:calc(-1 * (var(--wing) + var(--flow-pad))); }` — від 960px панелі це 900 + 2·(wing+30),
  тобто рівно до 12px від внутрішнього краю. Перемикач — `.widebtn` у `.meta` (або `m.wide` від артефакту),
  стан не зберігаємо: після перезавантаження широке знову вузьке.
  Без container queries `--wing` лишається `0` — широке дорівнює колонці; це прийнятний запас.
- **Скрим і скрол.** `#feed{overflow-y:auto; overscroll-behavior:contain;
  scroll-padding-block:calc(var(--dock-h) + 16px) calc(var(--comp-h) + 16px);}`
  `#flow{padding-block:calc(var(--dock-h) + 18px) calc(var(--comp-h) + 18px);}`
  `.msg{scroll-margin-block-start:calc(var(--dock-h) + 24px);}`
  `#chat::before,#chat::after{content:""; position:absolute; left:0; right:0; z-index:6; pointer-events:none;}`
  `::before{top:0; height:calc(var(--dock-h) + 26px); background:linear-gradient(to bottom,
  var(--bg) 0 14px, color-mix(in srgb, var(--bg) 78%, transparent) 60%, transparent);}`
  `::after{bottom:0; height:calc(var(--comp-h) + 30px); ... to top ...}`.
  `--dock-h/--comp-h` ставить JS: `dockMeasure()` через `ResizeObserver` на `#pin-dock` і `#composer`
  (`dock.offsetHeight + "px"`), інакше висота дока з двома рядами чипів накриє перше повідомлення.
- **Бульбашки.** Док: `position:absolute; top:12px; left:50%; translateX(-50%);
  width:min(calc(var(--flow) + 2*var(--flow-pad)), calc(100% - 24px));` `--r3`, `--line`, `--bg` 84% +
  `backdrop-filter:blur(14px)`, `box-shadow:0 14px 34px rgba(0,0,0,.3)`, `z-index:7`. Композер — дзеркально
  (`bottom:12px`). Док не має стану: порожній — один `.dock-empty`, і висота все одно міряється.
- **Чипи.** Висота 46px, `.chip{max-width:284px; flex:none}`; `.chip.shelf{border-style:dashed;
  background:transparent}`; прев'ю — `#pinpreview` (fixed, поза `#chat`: `container-type` робить `#chat`
  контейнером і для fixed-нащадків).
- **Смуга.** `#app{display:grid; grid-template-rows:56px minmax(0,1fr); height:100dvh; overflow:hidden}`;
  `#main{display:grid; grid-template-columns:minmax(0,1fr); min-height:0; padding:12px 14px 14px}`.
  `#system` — `position:fixed; inset-block:0; left:0; width:min(380px,100%); transform:translateX(-102%);
  transition:transform .22s`; `.on` — `transform:none`.

## 3. Файли, маршрути, слова

| Варіант | Плюс | Мінус |
|---|---|---|
| A. Усе в `page.html` (як тепер) | нуль правок тестів та i18n | ~5000 рядків; пʼять спек в одному файлі |
| B. Лише `page.css` | тести чіпають один блок | JS лишається 3400 рядків |
| **C. `page.html` + `page.css` + `page.js` (рекомендовано)** | три відповідальності, класичний скрипт, `tr()` видно | 4 точкові правки (нижче) |
| D. ES-модулі | справжня ізоляція | `node --check` потребує `.mjs`; `tr` треба імпортувати або в глобал; `/*WORDS*/` мусить встигнути до модуля |

C: **`page.html`** — розмітка, `<html lang="uk">`, `<link id="page-css" rel="stylesheet" href="/page.css">`,
в кінці тіла `<script>/*WORDS*/</script>` (як сьогодні, перший рядок скрипта), далі
`<script src="/page.js"></script>`. `page.css` — увесь CSS, включно `@font-face` і `.w …` правила для живих
віджетів. `page.js` — увесь JS, секції `/* ── оболонка ── */` … у порядку сьогоднішнього файлу.
Порядок важливий: класичний скрипт бачить `tr`, `trn`, `I18N`, оголошені `const` у попередньому класичному
скрипті; `data-t` заповнюється до запуску `page.js`, тому вузли, створені з JS, кличуть `tr()` самі.
`defer`/`async` не ставити — JS чіпляється до розмітки, яка вже розібрана.

**Маршрути** (`server.py`, поруч із `page` і `font`):

```python
PAGE_CSS = Path(__file__).with_name("page.css")   # нове
PAGE_JS = Path(__file__).with_name("page.js")     # нове

async def page_css(request):
    return web.Response(body=PAGE_CSS.read_bytes(), content_type="text/css",
                        headers={"Cache-Control": "no-store"})
async def page_js(request):
    return web.Response(body=PAGE_JS.read_bytes(), content_type="text/javascript",
                        headers={"Cache-Control": "no-store"})
# у add_routes: web.get("/page.css", page_css), web.get("/page.js", page_js),
```

`guard` пропускає все, що не `/api/` і не `/screens/`, тож далеке вікно їх дістане (як шрифти); перелік
`REMOTE_WINDOW_ALLOWED` не змінюється, census у `tests/heare/test_api.py` такі шляхи пропускає — оновити
лише коментар «the page, its css, its script and its fonts are public by design». `no-store`, бо сторінку
замінюють `make live`-ом, а без валідаторів браузер тримає старий JS. `SHEET` і `vars()` у `page.js`
привʼязати явно: `document.getElementById("page-css").sheet.cssRules` замість `document.styleSheets[0]`
(інакше порядок `<link>` і `<style id="own">` стає контрактом), а `vars()` читає `$("chat")`, не `$("stage")`.

**Слова.** `words.page()` замінює перший `/*WORDS*/` і рядок `<html lang="uk">` — обидва мусять лишитися
дослівно. `scripts/heare_babel.py` шукає `tr(`/`trn(` по всьому файлу, а розмітку — до першого `<script>`;
для `page.js` треба нова функція (не новий файл):

```python
def script(fileobj, keywords, comment_tags, options):
    """tr()/trn() у власному скрипті сторінки: ті самі ключі, без розмітки."""
```

і рядок у `babel.cfg`: `[heare_babel.script: packages/heare-brain/heare/api/page.js]`. Для `page.css`
екстрактор не потрібен (слів немає). Після цього — `make i18n`.

**Тести читають файли** (`tests/heare/test_page.py`, шапка):

```python
API = Path(__file__).resolve().parents[2] / "packages/heare-brain/heare/api"
PAGE = (API / "page.html").read_text(encoding="utf-8")   # розмітка
SCRIPT = (API / "page.js").read_text(encoding="utf-8")   # був join <script>
STYLE = (API / "page.css").read_text(encoding="utf-8")   # нове
```

`SCRIPT` більше не збирається регексом — це і є вся правка; `node --check` пише `SCRIPT` у `page.js`
і працює, бо скрипт класичний.

## 4. Переїзд: сім кроків, кожен робочий

Кожен крок — окремий коміт, `make test` зелений, сторінка на `/` відкривається. Старий код не видаляється
до M7, тому відкат кроку — `git revert` без залежностей.

**M1. Файли (без змін вигляду).** (а) CSS → `page.css` + `<link>`; `test_the_drawer_styles_new_rows…`
читає `STYLE`; `test_the_page_brings_its_own_fonts` сканує `url\(/fonts/…\)` у `page.css`. (б) JS →
`page.js` + `<script src>`; шапка тестів вище. (в) `babel.cfg` + `script()`, ключі в `PAGES` тесту мови.
Перевірка: сторінка піксельно та сама, `make test`, `make lint`, `make i18n` без дифу, `node --check`.
**M2. Рама.** `#app` — два ряди; `#mbar` стає `#top` (ті самі `#mface/#mphase/#name`), `#left` → `#system`,
`#partcol` і `#stage` — секції всередині `#sheet` (canvas лишається з `#stage` і `#live`, щоб `vars()`,
`restyle()` і `@scope (#stage)` не зламалися); `#talkcol` зникає, `#talk` → `#feed>#flow`, `#talkbar` →
`#composer` з тим самим `#say`, `#chips` — у бульбашку композера; додати порожній `#pin-dock` і скрим-плейсхолдери.
JS: `feedAppend(node)`, `feedToEnd()`, `vars()` читає `$("chat")`, `$("talk").scrollTop` →
`feedToEnd()` (4 місця: `line`, `aside`, `loadSessions`, гілка `reply`). Композер шле, транскрипт іде,
стоп і памʼять працюють. Полотно — за кнопкою в `.sysfoot`; на `drawn({op:"put"})` при закритому
`#sheet` — рядок у стрічці «нове на екрані» (посилання відкриває `#sheet`), щоб блок не зникав мовчки.
**M3. Стрічка.** `.msg` + `.meta` + `feedRender(turns)`: `renderFeed` замінює `replaceChildren` у
`loadSessions`; потоковий `reply` дописує в поточний `.msg`; `aside` → `.feed-note`; `#empty` показується,
коли в `#flow` немає `.msg`; `feedToEnd()` лише якщо людина в межах 80px від низу. Кожен `.msg` несе `data-mid`
(= `"<session>/<n>"`, як у спеці закріпленого) і `data-session`: на них тримаються піни, чипи й прев'ю.
**M4. Док (локально).** `pins` — `localStorage["heare.pins"] = {sessionId: [messageId]}`, поки немає
`GET /api/pins` (новий маршрут — власність спеки закріпленого). `pinButton(msg)`, `dockRender()`:
trail = закріплені поточного сеансу, чий низ вище `#feed` + `--dock-h`; shelf = піни інших сеансів;
поріг, гістерезис і перерахунок (rAF на `scroll`, не `IntersectionObserver`) — зі спеки закріпленого;
`previewShow(id, chip)` клонує `.msg` у `#pinpreview`. Чип переживає перезавантаження, веде до повідомлення і відкриває прев'ю на hover.
**M5. Блиск.** `dockMeasure()`, скрими, `env(safe-area-inset-bottom)` у композері, `#topmic`/`#topsound`
(мікрофон = `#hear`-обробник, звук = `#speak` + `aloud`), `#lastact` з подій `said/heard/task/canvas`,
`#model` = модель із `vitals()`. Видалити CSS `#talkbar/#talkdrawer/#talktoggle`.
**M6. Аркуші.** `#settings` і `#mind` переносяться в `#sheetbody` без зміни розмітки й ID;
`openSheet(title, body, size)`/`closeSheet()`; кнопки `.sysfoot` (памʼять/деталі/налаштування) і
`#opensettings`; `body.sysopen`/`body.sheetopen`: на вузькому — рівно одна шухляда, на широкому — аркуш
звужується (`body.sysopen .sheet{width:min(430px, calc(100vw - 396px))}`). Старий перехід
«після відновлення — на backups» лишається.
**M7. Крила й прибирання.** `.msg.wide` + `.widebtn`; `.blk.l` у канвасі мапиться на wide; коли стрічка
вміє артефакти (спека стрічки) — `#live` переїжджає в `#flow`, `#stage` видаляється; разом із ним — мертвий
CSS/JS і `docs/product/page.md`.

## 5. Вузькі екрани, слова, тести, відкат

- **≤900px:** `#app` ряди `52px minmax(0,1fr)`; `#main{padding:8px}`; `#chat{--wing:0px}`,
  `.msg.wide{width:100%; margin-inline:0}`; `#pin-dock`/`#composer` — `calc(100% - 16px)`, `top/bottom:8px`;
  `#flow{padding-inline:14px; gap:22px}`; `#text{font-size:16px}` (iOS не зумить); `#system` і `.sheet` —
  `width:100%`, без лівого/правого бордера; `.pinbtn{opacity:.55}`. **≤380px:** ховати `#lastact` і
  `.modelchip`, не `#stop`.
- **≤1200px:** `.lastact` зникає; `prefers-reduced-motion` — без анімацій і переходів.
- **Нові ключі** (англійською, `tr()`/`data-t`; `make i18n`, потім українська в `.po`, інакше падає
  `test_the_catalog_knows_every_line_the_code_marks`):

| ключ | українською |
|---|---|
| `microphone` | мікрофон |
| `sound` | звук |
| `Pinned` | Закріплене |
| `Nothing is pinned yet` | Поки нічого не закріплено |
| `Pin` | Закріпити |
| `Unpin` | Відкріпити |
| `pinned` | закріплено |
| `another conversation` | інша розмова |
| `Go to the message` | До повідомлення |
| `full width` | на всю ширину |
| `System` | Система |
| `new on the screen` | нове на екрані |

  Повторно вживати наявні: `Conversation`, `Sessions`, `Memory`, `Settings`, `Close`, `close`, `remove`,
  `last: {when}`, `hear the reply aloud`, `speak and hear`, `attach a file`, `Stop everything?`.
- **Тести, які треба оновити:** `test_page.py` (шапка + CSS-перевірки в `STYLE`);
  `test_language_rule.py` (`PAGES += [page.js, page.css]` — інакше правило мови тихо слабшає);
  `test_api.py::test_the_built_in_page_is_served` (`"Controls" in page` → `id="chat"`, `id="composer"`,
  `id="rail"`; лишити `id="pairing"`, `Devices`, `Connect a device`; додати GET `/page.css` і `/page.js`
  з правильним `content_type`); `test_api.py::test_the_page_brings_its_own_fonts` (сканує `page.css`).
  Новий `tests/heare/test_page_shell.py`: наявність `#top #chat #pin-dock #feed #flow #composer #system #sheet
  #pinpreview #veil #toast`; `--wing`, `scroll-padding-block`, `#chat::before/::after`; `.chip.shelf`;
  `#pinpreview` поза `#chat`; жодного `<script>` усередині `#flow`; демо-фід лише за прапорцем.
- **Відкат.** Крок = коміт; до M7 старий DOM живий, тому достатньо `git revert`; жива машина —
  `make rollback` або `make live` з попереднього коміту. `localStorage` (`heare.aloud`, `heare.page`,
  `heare-restoring`) не перейменовувати; новий лише `heare.pins`.

## 6. Приймання: макетна стрічка до змін у мозку

`GET /?demo=1` (лише за прапорцем; `const DEMO = new URLSearchParams(location.search).get("demo") === "1"`)
малює `DEMO_FEED` — 12 повідомлень (текст, image, table, ask, step, одне `.wide`) і `DEMO_PINS`
(3 trail + 2 shelf), тим самим `feedRender`/`dockRender`, без жодного `/api/sessions`; `#knock`/`state` не чіпає.
Критерій: на 1440×900 і 390×844 сторінка збігається з `.sisyphus/ui/sketch/` за розташуванням панелі, дока,
композера, скриму й чипів; стрічка скролиться під бульбашками, широке повідомлення доходить до крил;
консоль без помилок; `make test` зелений.

## 7. Ризики й відкриті питання

**Ризики.** (1) Розділення файлів ламає шість текстових перевірок і екстрактор i18n одразу — M1 окремим комітом, потім `make i18n`, інакше `.po` відстає. (2) `container-type` на `#chat` робить його
контейнером і для fixed-нащадків: `#pinpreview`, `#sheet`, `#toast`, `#veil` мусять лишитися поза `#chat`.
(3) `#stage` у `#sheet` легко зламати `vars()`/`restyle()`/`@scope` — ID `stage` зберігати доти, доки
`#live` не переїде. (4) `--dock-h` без `ResizeObserver` накриє перше повідомлення, коли чипів стане два ряди.
(5) `test_the_stream_reconnects_and_knocks` вимагає рівно одного `stream.onopen =`; не додавати другий.
(6) `test_a_live_widget_cannot_read_memory_or_say` звіряє `BRIDGE` дослівно — переносити в `page.js` байт у байт.

**Відкриті питання.** (1) Куди живий канвас між M2 і артефактами — `#sheet` (ця спека) чи одразу перше
`.msg.live` у стрічці? Залежить від порядку кроків спеки стрічки. (2) `GET /api/pins`: хто пише ендпоінт і
коли локальні піни переїжджають у мозок (і чи видалення сеансу видаляє їх на сервері)? (3) Стабільний
`id` повідомлення: сторінка рахує `n` з масиву `turns`, сервер — рядки `turns.jsonl`; чи збігаються вони, коли рядок логу зламаний — хто тримає
rich-message? (4) `.chip` зайнятий вкладеннями: перейменувати їх у `.filechip` у M3 чи лишити докові
`.pinchip`? (5) Чи лишається `?demo=1` назавжди, чи видаляється після M7? (6) На широкому екрані —
шухляда й аркуш одночасно (як у скетчі), чи всюди «одна шухляда»?
