# Повідомлення й артефакти (msg-model)

*Специфікація перебудови heare page, 11 листопада 2026. Один із п'яти паралельних архітекторів; код не змінено.*

## 1. Мета і межа

Чат — головне вікно сторінки, артефакти — повідомлення, окремої дошки немає. Тут: модель повідомлення,
зберігання, HTTP/SSE, міграція канвасу. Вигляд стрічки, піна-док, композер і шторка — інші специфікації;
**піни, чипи й док належать `pins.md`** — тут лише те, як повідомлення з ними пов'язане.

Межа не рухається: `heare-core` не знає про артефакти, сесії й диск. Усе — в `heare-brain/heare/api/`.
Модель уже має механізм: `Surface` (`forms.py`). `Core.system()` (conversation.py:897-900) додає
`output.context()` до промпту, `Core._verbs()` — `output.verbs()` до інструментів. Канвас зроблений так;
повідомлення робимо так само.

**Як зараз.** `POST /api/message` → `Core.hear` → `_exchange` → `Kept.keep` пише `sessions/<id>/turns.jsonl`
(`{role,text,at,files}`) і пам'ять; події `reply` (шматками) і `said` (цілий текст). Канвас: `canvas_put` →
`Canvas.put` → спільний `canvas.json` → подія `canvas`; сесії в нього немає, порядку з розмовою теж, пінів
немає. Сесія — тека `sessions/<32hex>/` з `turns.jsonl` і `files/`; `index.json` тримає
`active/items/turns/unread`; перемикання — `Core.hydrate`. Стрічка = `GET .../turns` + `GET /api/canvas` окремо.

## 2. Схема повідомлення

Один рядок `turns.jsonl` = одне повідомлення. `id` = `"<session_id>/<n>"`, як уже домовлено в `pins.md` і
`shell.md`: `n` — порядковий номер повідомлення з 0 (рядки-пати не рахуються), незмінний, бо лог лише
дописується. Це дає прямий доступ до повідомлення й до піна без індексу.

```json
{"id":"a3f1…/7","session_id":"a3f1…","role":"assistant","at":1762893000.12,
 "kind":"html","text":"Погода · Бурштин: +1°, вітер 6 км/год",
 "html":"<h3>Бурштин</h3><p class=\"big\">+1°</p>",
 "artifact":{"label":"weather","title":"Погода · Бурштин","live":false},
 "width":"w","status":"done","files":[]}
```

| поле | значення |
|---|---|
| `id` | `"<session_id>/<n>"`; стабільний, унікальний у мозку |
| `session_id` | 32 hex, тека сесії |
| `role` | `user` \| `assistant` (крок і питання — `assistant`) |
| `at` | epoch-секунди, як `sessions.add` сьогодні |
| `kind` | `text`, `html`, `image`, `audio`, `video`, `link`, `ask`, `step` |
| `text` | завжди; чисті слова для чипа, пошуку, моделі, a11y |
| `html` | лише `kind:"html"`; після `canvas.clean()`, ≤ `BLOCK_CHARS` (6000) |
| `artifact` | payload за `kind`; для `text` відсутній |
| `width` | `s` \| `w`; на мобільному `w` = вся ширина |
| `status` | `done`, `failed`, `stopped`, `withdrawn`; `streaming` лише в пам'яті |
| `files` | як сьогодні, лише `role:"user"` |

`text` для `html` — `canvas._text(html)[:400]`; `image/audio/video` — підпис; `link` — title; `ask` —
what/question; `step` — рядок кроку. **Пін — не поле.** Якір в окремому store: ціль — той самий `id`, тому
пін переживає `patch` і не копіює вміст; видалення сесії видаляє її піни (див. `pins.md`).

## 3. Артефакти за видами

| `kind` | `artifact` | хто створює | width |
|---|---|---|---|
| `text` | — | `Kept` (відповідь), двері (слово людини) | `s` |
| `html` | `{label,title,live,block?}`; label ∈ card,table,weather,game,list,code; live = в html є `<script>`; `block` — старе ім'я канвасу | `show`, міграція | `w` для table/weather/game |
| `image` | `{file,name,alt,title}`, file з `files/` сесії | `show_media` | `w` |
| `audio` | `{file,name,mime,title}` | `show_media` | `s` |
| `video` | `{file,name,mime,title}` | `show_media` | `w` |
| `link` | `{url,title,host}`, лише http/https | `show_link` | `s` |
| `ask` | `{id,form,task,executor,what,why,state,answer}`; form ∈ permission,question; state ∈ waiting,allowed,denied,answered,cancelled | `Timeline.follow` з `permission*`/`question*` | `s` |
| `step` | `{task,executor,step,state}`; state ∈ running,done,failed,stopped | `Timeline.follow` з `task` | `s` |

Один живий екземпляр: сторінка тримає щонайбільше один `iframe` для `live:true`, решта — статичний текст і
«запустити». `show` не редагує стару картку: нове повідомлення = нова картка (семантику `canvas_put` «на
місце» прибрано). `ask` — та сама `Permission`/`Question`; кнопки шлють `POST /api/permission/{id}` і
`POST /api/answer/{id}`, як сьогодні; ключ `(form,id)`, тому реплей подій не дублює картку. `step` — один на
`Task`: перша подія `task` створює, наступні `patch`.

## 4. Де живуть

- **Диск — джерело правди.** Той самий `sessions/<id>/turns.jsonl`, рядок розширено (старі читачі ігнорують
  ключі). Окремий `messages.jsonl` не заводимо: два логи розійшлися б.
- **Дописування, не переписування.** `Sessions.add(session, role, text, *, kind="text", html="", artifact=None,
  width="s", status="done", files=None, unread=False, at=None) -> dict` пише рядок, рахує `n`, повертає
  повідомлення; оновлює `updated`/`turns` (лічильник — лише `text`), `title`, `unread`.
  `Sessions.patch(session, n, **fields)` дописує `{"patch":{…},"for":"<session>/<n>","at":…}`; `read` згортає
  пати в порядку рядків. Жодного `os.replace` для повідомлень.
- **Старі рядки** дістають `id` позиційно: `"<session>/<n>"`, `kind:"text"`, `width:"s"`, `status:"done"`.
  Нічого не мігруємо — id виникає при читанні, тому він стабільний між перезапусками.
- **Доступ за id.** `Sessions.message(session, n)`; `n` — індекс серед повідомлень, не рядків логу.
  `GET /api/sessions/{id}/messages/{n}` віддає одне повідомлення (ховер-прев'ю піна). Індексу не треба.
- **Пам'ять процесу.** `core.history` лишається 12 ходами; стрічка завжди з диска — дві вкладки бачать те
  саме. `state.journal` (200 подій) — не історія повідомлень.
- **Модель бачить лише слова.** `for_model` пропускає `kind != "text"`; артефакти — лише через
  `Timeline.context()` (≤900 символів: id, label, title, перші 120 символів text, width).
- **Стрімінг.** `reply` лишається подією; сторінка малює з нього тимчасову бульбашку. Повідомлення пишеться
  одне, коли хід скінчився (`said` → `Kept` → `add`). Перезавантаження посеред відповіді покаже її після
  кінця — свідомо, без чернеток на диску.

## 5. HTTP і SSE

Стартовий стан сторінки — без нового агрегату: `GET /api/sessions` → `active`, далі
`GET /api/sessions/{id}/turns` (масив повідомлень; сумісний зі старим JS) і `GET /api/pins` (`pins.md`).
`?limit=&before=` на `/turns` — необов'язкові, типово всі.

| метод і шлях | тіло / відповідь | доступ |
|---|---|---|
| `GET /api/sessions/{id}/turns` | масив `Message` (сумісність; `text`, `role` на місці) | локально |
| `GET /api/sessions/{id}/messages?before=&limit=&kinds=` | `{session_id, messages[], has_more}` | локально |
| `GET /api/sessions/{id}/messages/{n}` | `{session_id, message}` | локально |
| `POST /api/sessions/{id}/messages/{n}/width {width}` | `{message}` | локально |
| `POST /api/message {text, files[]}` | `{ok, session_id, message}` | вікну |

`kinds` — назви через кому; невідома назва ігнорується (як `?kinds=` у `/api/events`). `POST /api/message`
пише рядок **до** `core.hear` (слово людини видно негайно) і повертає його; `Kept` більше не пише хід
людини, лише пам'ять. Без тексту й без файлів — `400`, як сьогодні. Голос: двері пишуть слово людини, коли
`addressed` (типово правда); при `addressed:false` (мова в кімнаті) не пишуть, а `Timeline.follow` додає його
з події `heard`, коли ядро таки відповіло (дедуп за останніми 5 повідомленнями) — тиша не лишає сліду в
стрічці. `DELETE /api/sessions/{id}` кличе `pins.forget(session)` перед `sessions.delete`.

**SSE (`GET /api/events`)** — новий вид `message`; він зі словами, тому **не** в `STATUS_KINDS` і не
проходить `?kinds=`:

| kind | data | коли |
|---|---|---|
| `message` | `{op:"add", session_id, message}` | кожен `Sessions.add` |
| `message` | `{op:"patch", session_id, id, patch}` | ширина, стан ask/step, status |
| `message` | `{op:"withdraw", session_id, id}` | legacy `canvas/remove` |

`reply`, `said`, `task`, `permission`, `question` не змінюються: голос, Mac, панель живуть як жили. Сторінка на
`message` додає/патчить `[data-mid]`, на `said` лише озвучує. `pins`-подія й піни — у `pins.md`. `Sessions`
отримує `emit` у конструкторі (`Sessions(root, emit=...)`), щоб запис і подія були одним актом; тести кличуть
`Sessions(tmp)` без emit.

## 6. Міграція канвасу

`Timeline.migrate_legacy(canvas_file)` у `build()` після вибору активної сесії, до відкриття дверей.
Ідемпотентно: маркер `canvas.migrated.json` біля `canvas.json` (`{"v":1,"at","session_id","blocks":[{"block","id"}],"css"}`);
`canvas.json` **не** переписуємо й не видаляємо — відкат лишає старий канвас живим.

| старе | нове |
|---|---|
| `blocks[i][0]` | `artifact.block`; title — перший `<h3>`/`<h2>` або id |
| `blocks[i][1]` | `html` (ще раз `clean`), `text` = `_text` |
| size `s`/`m`/`l` | width `s`/`w`/`w` |
| `<script` у html | `artifact.live:true` |
| порядок блоків | порядок повідомлень; `at` = mtime + i×0.001 |
| `css` | у маркер і `legacy_css` у відповіді `GET /api/canvas`; **не застосовується** |
| сесія | `sessions.active` на першому старті після оновлення |

Старі маршрути — тонкі шиби над повідомленнями:

- `GET /api/canvas` → `{blocks:[{id,html,size}], css}` з `html`-повідомлень активної сесії (`w`→`l`, `s`→`s`;
  `id` — `artifact.block` або `"<session>/<n>"`; новіші 30) — стара сторінка під час розкочування не біліє.
- `POST /api/canvas/put {id,html}` — останнє не-withdrawn з `artifact.block == id`: є → `patch` html/text, нема
  → `add` `kind:"html"`, `artifact.label:"canvas"`. Стара семантика «на місце» зберігається.
- `POST /api/canvas/remove {id}` → `status:"withdrawn"` + `message patch` (`{removed:true}`); вміст не
  видаляється.

`Canvas` лишається класом і в `tests/heare/test_canvas.py`, але **не** додається як вивід у `serve()`;
`canvas.clean()`/`_text()` використовує `Timeline`. Видалення `Canvas` — після одного релізу.

## 7. Модель: дієслова й промпт

`Timeline` — `Surface` + `Output`, `name="page"`, додається в `serve()` **після** `Screen` і `VoiceOut` (щоб
`_output_for`-запасний лишився `Screen`). Три дієслова в `Timeline.verbs()`:

- `show {html, title, say, label?, width?}` → `kind:"html"`; `say` обов'язковий; label з html (`<table`→table,
  `<script`→game, інакше card); width w для table/weather/game. Опис — як `_PUT` у canvas.py: коли показувати,
  helpers (`big|muted|row|meter|pill`), `data-say`, живий `<script>` із `heare.get('system'|'state'|'tasks')` і
  fetch погоди, «не вигадуй живі числа».
- `show_link {url, title, say}` — лише http/https; інакше `_("That link cannot be shown.")`.
- `show_media {file, kind:"image"|"audio"|"video", title, say}` — file мусі бути в `files/` цієї сесії
  (`Sessions.stored`), інакше `_("That file is not in this conversation.")`.

Відповідь дієслова — `say` або `_("It is on the page.")`; ядро мовить її лише коли модель не сказала нічого
(`_acknowledging`). `canvas_*` з пропозиції прибираємо (HTTP-шиби лишаються). `tools/toolbox.py`: прибрати
`canvas_view` з `read_only`/`lookup`; відповідний тест оновити. `prompt/body.py` **не** змінюється: він
перелічує частини, а картки — це `Surface.context()`, який `Core.system()` додає сам. `prompt/persona.py`:
«The canvas is the heare page…» → «The page is the chat in the browser…» і одне речення: «What you show becomes
a card in the chat; the words stay short and are still spoken.»

## 8. Файли, функції, тести

Нові: `heare/api/timeline.py` (`Timeline(sessions, emit)`: `post`, `patch`, `withdraw`, `view`, `start`/`follow`,
`context`, `verbs`, `summary`, `migrate_legacy`, `_SHOW`, `_SHOW_LINK`, `_SHOW_MEDIA`; `task` пам'ятає сесію
походження, як `follow_tasks`); `tests/heare/test_timeline.py`.

Змінені: `api/sessions.py` (поля, `add(**)`, `patch`, `message(session, n)`, `for_model`-фільтр, `Kept` = пам'ять
+ `ATTACHED`-обрізання); `api/server.py` (`Timeline`, `app["timeline"]`, `serve()` без `Canvas`, нові маршрути,
двері, подія `message`, шиби, `follow_tasks` через `Sessions.add`); `prompt/persona.py`; `tools/toolbox.py`;
`docs/product/protocol.md` §2 (вид `message`); `docs/product/page.md` (канвас → стрічка);
`tests/heare/test_api.py` (нові маршрути в `LOCAL_ONLY`, census, 401, події), `test_sessions.py` (`/turns` має
`id`), `test_canvas.py` (міграція), `test_page.py` (текстові перевірки стрічки).

Вивід `Timeline` у тестах, що кличуть лише `build()`, додають самі:
`room.core.add_output(client.app["timeline"])`.

## 9. Порядок (кожен крок лишає `make test` зеленим)

1. `sessions.py`: `id`=`"<session>/<n>"`, розширений `add`, `patch`, `message(session,n)`, `for_model`-фільтр
   + тести. Стара сторінка не помічає.
2. `Timeline` без дієслів: `post`/`patch`/`view`/`summary`/`follow`, реєстрація в `build`/`serve`, подія
   `message` + тести.
3. Міграція + шиби `GET/POST /api/canvas*`; `Canvas` більше не вивід. Тест: старий `canvas.json` →
   повідомлення, другий старт не дублює.
4. Двері пишуть слово людини; `Kept` — лише пам'ять; `POST /api/message` віддає `message`.
5. `show`/`show_link`/`show_media`, `context()`, persona; прибрати `canvas_*` з пропозиції.
6. Маршрути `messages/{n}`/`width` + census-тести.
7. Сторінка (інша специфікація) читає `/turns`; `make i18n`; `make live`; відкат лишає `canvas.json` і старі
   рядки читабельними.

## 10. Тести

- `test_timeline.py`: кожен `kind` дає валідний рядок; `clean` вирізає `onclick|javascript:|iframe`; html >6000
  обрізано; width за замовчуванням; `show_link` відкидає `javascript:`/`data:`; `show_media` — чужий file id;
  `withdraw` прибирає з `context()`; `context()` ≤900; `follow` створює один `step` на `task` і патчить його;
  `permission_closed` → `state:"allowed"`; реплей не дублює; `id`-форма `"<session>/<n>"`.
- `test_sessions.py`: id стабільний між `read`; старий рядок отримує `"<session>/<n>"`; `patch` згортається;
  `turns` рахує лише `text`; `for_model` без `html`; `/turns` віддає `id`.
- `test_api.py`: 401 без токена на всі нові; `POST /api/message` повертає `message`, і він перший у
  `GET .../messages`; SSE дає `message add`/`patch`; `before`-пагінація; `width` патчить; census бачить нові
  маршрути в `LOCAL_ONLY`; `GET .../messages/{n}` для чужої сесії.
- `test_canvas.py`: блоки → `kind:"html"` активної сесії, size→width, css у маркері, другий старт не дублює; шиби
  «на місце»/`withdrawn`.
- `test_page.py` (текст, без браузера): `api(\`sessions/${session}/turns\`)`, `e.kind === "message"`,
  `data-mid`, `MAY_GET` без `memory|history|canvas`, міст без `say`, `sandbox="allow-scripts"` без
  `allow-same-origin`.
- `test_language_rule.py`: нові `_()` перекладені; `make i18n`.

## 11. Ризики

- `canvas.css` більше не діє: дані в маркері, вигляд — ні (тема поза цим файлом).
- Блоки дістаються сесії, активній на момент оновлення; якщо це не та розмова — артефакти не в тому місці.
- Позиційний `n` спирався б на «лог лише дописується»: будь-яка майбутня компакція сесії зсуне id й піни.
- Живі віджети перезапускаються; стан у iframe втрачено. Подвійний текст біля дозволу: вербатимний рядок і
  картка `ask`.
- `for_model` не бачить вмісту старих карток — лише огляд ≤900. Піни після restore можуть вказувати на
  неіснуюче; чистку робить `pins.md`.
- `message` несе до 6 КБ html кожному вікну; часті `patch` кроків помітні, тому крок один на задачу.

## 12. Відкриті питання

1. Чи може людина прибрати повідомлення зі стрічки (і що на його місці)? Зараз лише `withdrawn` для
   legacy-канвасу.
2. Чи має тема канвасу (`canvas.css`) переїхати в тему сторінки — і хто нею володіє.
3. Чи потрібне моделі «оновити картку на місці» (`replace`), чи нове повідомлення — завжди нова картка.
4. Чи можна вікну з іншої машини читати стрічку (зараз `/turns` і нові маршрути — локальні; `/api/history`
   лишається єдиним вікном назовні).
5. Чи показувати в `context()` картки з інших сесій (пропоную — ні).
