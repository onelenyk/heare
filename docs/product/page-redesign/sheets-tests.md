# Пам'ять, деталі, налаштування: інвентар, міграція, тести (sheets-tests)

> Специфікація перебудови `packages/heare-brain/heare/api/page.html`; production-код,
> тести й `.sisyphus/ui/sketch/` не змінюються. Прочитано: `page.html` (3480), `server.py`,
> `mind.py`, `sessions.py`, `api/machines.py`, `api/backup.py`, `api/heard.py`, `features.py`,
> `core/config.py`, `core/keys.py`, `test_page.py`, `test_language_rule.py`, `test_api.py`,
> `test_sessions.py`, `test_i18n.py`, `scripts/heare_babel.py`, `sketch/app.js`, `style.css`.

Правило: **перестилювати, не переписувати**. Кожен рядок §1 мусить мати місце в новому DOM і ту саму дію; гачки тестів (`id="mind-tab-*"`, `id="mind-panel-*"`, `.mind-item`, `.mind-tab`, `#mind`, `.source-row`, `.backup-row`) лишаються.

## 1. Інвентар: старе проти скетчу

✅ є · ◐ частково · ✗ немає (втрата). Джерело — наявний endpoint; нових полів не вигадуємо.

### 1.1 «Факти» (`GET /api/mind`)

| Дані | Старе | Скетч |
|---|---|---|
| `counts.pending/confirmed/reminders`, `review.last`, `consolidation.last`; `review.hour` | 4 рядки pult + 5 чипів; повні числа при 50 показаних; `hour` у DOM ні | ◐ чипи без pult, підписи інші |
| `facts.pending[]`: `id,text,source,confidence,valid_from,created_at,status` | `.mind-fact.pending`: текст, джерело, `confidence %`, дата, Confirm/Reject | ◐ текст, дата, дії; ✗ `confidence`, `id`, `status`, 5 із 6 джерел `MIND_SOURCE` |
| `facts.confirmed[]` + `trust/predicate/value` | те саме + Forget двічі, «Forget for sure?»; `trust/predicate/value` у JSON, у DOM ні | ◐ Forget є; ✗ confidence, словник джерел, `trust` |
| стани | «nothing awaits a decision.», «no settled facts yet.», «memory is unavailable now», «failed — try again» | ✗ |

### 1.2 «Нагадування» (`reminders[]`)

| Дані | Старе | Скетч |
|---|---|---|
| `id,what,about,not_before,until,made,weight` | `what`, `mindTime`, «in {m} min / in {h} h / in {d} d / overdue / in less than a minute», Cancel | ◐ `what`, час, Cancel; ✗ `about/until/made/weight/id`, формати |
| порожнеча й текст | «nothing to remind.» + «say «remind me…», and it will appear here.» | ✗; текст скетчу «приходять… на дошку» — дошки немає, переписати на «в чат» |

### 1.3 «Журнал» (`GET /api/mind/journal?limit=80`)

| Дані | Старе | Скетч |
|---|---|---|
| `items[]`: `id,at,kind,title,text,meta[]` | `mindItem` (спільний із пошуком): title, kind, `mindStamp(at)`, text, meta (source, trust, channel, status) | ◐ час, мітка, текст; ✗ `meta`, `id`, 3 з 8 видів (`heard/said/task/verb/permission/permission_closed/forget/profile_edited`) |
| Forget `heard`/`said` | `POST /api/mind/journal/forget {ids}` після `twice()`, title «It will be neither in the journal nor in search» | ◐ Forget на 2 рядках |
| стани й порядок | «reading the journal…», «the journal is empty.», «could not read the journal.» + Try again, число; сорт `at` спадаюче | ✗ |

### 1.4 «Пошук» (`GET /api/mind/search?q=`)

| Дані | Старе | Скетч |
|---|---|---|
| `said`, `items[]` | цитата відповіді в `#mind-search-said` (прихована коли порожня); `items` через `mindItem` | ◐ один статичний факт; ✗ `said` |
| стани | «searching…», «write what to recall.», «I recall nothing.», «the search failed» | ✗ |

### 1.5 «Слух» (`GET /api/heard?limit=80`)

| Дані | Старе | Скетч |
|---|---|---|
| `replies[]`: `turn,at,ms,text,truth,dropped,label,pieces[{name,ms,text,dropped,mean_db}]`, `marked,total` | рядок: play, label («to the assistant»/«background»), dropped→«dropped as made up», `mindTime + {s} s + {n} pieces`, text або «nothing recognised»; при `truth !== text` — «was» + truth | ◐ play, текст, 3 кнопки; ✗ `pieces/truth/dropped/ms/marked/total` |
| відтворення й дії | черга `/api/heard/{name}.wav`, пауза попереднього, ▶/■; Right / Correct (інлайн-форма «what you really said», «write what was said») / Background; `POST /api/heard/{turn} {label,truth}` | ◐ кнопка без звуку, без форми й endpoint |
| стани й пояснення | «reading the recordings…», «no recordings yet — …», «could not read the recordings.» + Try again; «The marks stay with the recording…» (тест!) | ◐ лише «9 правильних · 2 виправлені · 1 фон» |

### 1.6 «Профіль» (`PUT /api/mind/profile`)

| Дані | Старе | Скетч |
|---|---|---|
| `profile.blocks.user/threads/goals`, `profile.limits` (1200/1000/600) | 3 `<textarea>`, ліміт з сервера, лічильник `n / limit` з `.over` | ◐ 3 поля, статичні лічильники; ✗ ліміти з сервера |
| збереження | окремий Save: `PUT {key,text}`, «saved», «failed — try again», `mindDirty` не перетирає введене | ◐ один Save; ✗ ключі, помилки, dirty-захист |

### 1.7 «Джерела» (`GET /api/mind/sources`)

| Дані | Старе | Скетч |
|---|---|---|
| `name,found,records,waiting{user,feedback,decision},running{done,total},read,imported` | блок: назва, `about` (claude-code/engram/claude-mem), «{n} records», «not on this machine» | ✗ |
| імпорт | `Import {n} note(s)` на kind → `POST /api/mind/sources/{name}/import {kinds}` | ✗ |
| прогрес і завершення | смуга `done/total`, «You can close this…», банер `#imports-now` «Importing {name}: {done} of {total}»; «Read {read}, found {added}», «It stopped: {why}», «All imported: {n} note read.», «Nothing to import: …» | ✗ |
| undo й вступ | «Remove from heare», друге натискання → `POST …/undo`, «{n} facts came from here»; «…sends them to your model, {model}. Nothing is saved until you confirm it under Facts.» | ✗ **хибна**: погода/курс/календар, а не імпортери пам'яті |

### 1.8 «Копії» (`GET/POST /api/backups`)

| Дані | Старе | Скетч |
|---|---|---|
| `backups[]`: `name,made,size,sound,counts{facts,days,recordings,reminders},broken,version` | рядок: дата, «damaged», «without sound», `{mb} MB`, «{n} facts · {n} days of journal · {n} recordings · {n} reminders» | ◐ назва, розмір, дата; ✗ `sound/broken/counts/version` |
| `restored{name,at,made,aside}`, `folder` | «Restored {at} from the backup of {made}. …in backups/{aside}.», «kept in {folder}» | ✗ |
| створення й принесення | чекбокс «with sound (Hearing recordings)», `POST {sound}`; `POST /api/backups/bring` (zip), «checking…», «not taken: {why}» | ◐ без чекбокса; кнопка без станів |
| завантаження/відновлення й стани | `GET /api/backups/{name}`; Restore після `twice()`, опитування до 60 с, `sessionStorage["heare-restoring"]`, повернення на «Копії»; «making a backup…», «done: {mb} MB», «could not make a backup», «restored», «not restored: {why}» | ◐ без підтвердження, опитування, повернення, станів |

### 1.9 «Деталі» (стара вкладка Context, `GET /api/context`)

| Дані | Старе | Скетч |
|---|---|---|
| `turns[]`: `at,messages[{role,content}],verbs[]`, `limit`, `voice`, `tasks[{id,executor,status,started,request,brief}]` | `ctxMeasure` (system/talk/verbs/total), `ctxTimeline` з дельтою на хід, `ctxPicked` | ✗ фейкові тіки |
| показ ходу | gauge `~total tokens`, дельта, «fresh» файли з `«name»`, `fill` system/talk проти `limit`, legend, час | ◐ статичні gauge/fill/legend |
| системний промпт і розмова | `ctxSheet` + `promptBits`: блок «[що — …]», `~tokens`, `sourceOf` (persona/laws/canvas/body/api); лист `Person`/`Assistant`, мітка `fresh`, `core.history`; verbs — назва, опис, параметри `(a, b)` | ◐ 6 рядків `.ctxbit` з вигаданими токенами; ✗ текст промпту, розмова, verbs |
| задачі й стани | `#id executor`, status, brief/request, час, «No tasks yet.», «No turn yet.», оновлення кожні 3 с | ✗ |
| «Цей запуск», «Машини» | старе в pult: `session.started/turns/cost_usd/unpriced`, `running.version`; машини — 1.11 | ➕ у скетчі messages, replies+cost, uptime, version+commit, зведення машин — **з `/api/state` і `/api/machines`** |

### 1.10 «Налаштування» (`#settings`; `GET/POST /api/settings`, `GET/POST /api/keys`, `POST /api/keys/adopt`, `POST /api/pair-code`, `GET /api/features`, `POST /api/features/{id}/act`)

- **Мова**: `#lang` (uk/en) → `POST {language}` і перезавантаження. Скетч ✅.
- **Модель**: `#provider` (з `state.model.providers`), `#modelpick` + `#modelcustom` (id руками), `POST {llm_provider, <provider>_model}`, `#modelnote` («Saved…»/«Not saved: {why}»), `GET /api/models?provider=` (live/whitelist, «— default —», «searching…», «the list did not come — type the id.»). Скетч ◐: ✗ custom id і примітки.
- **Ключі**: `#keylist` (ім'я, `env/stored/none` → «in the environment»/«saved»/«none», «Use the one from the environment» → `/api/keys/adopt`, нотатка), `#keyname` (✓/—), `#keyvalue` + Save, `POST /api/keys {name,value}`, «Values are never shown — only where a key comes from.» Скетч ◐: є список/adopt, ✗ нотатки й перевірка.
- **Пристрої**: `#pair-code` → `POST /api/pair-code {code}`, Copy, «one-time, lives 5 minutes», «wait a couple of seconds…»/«could not get a code», hint. Скетч ◐: статичний код.
- **Фічі**: `GET /api/features` → `groups[{id,title,order}]`, `features[{id,title,about,group,order,state,note,controls[]}]`, `POST /api/features/{id}/act {name,value}`; стани `running/off/error/ready`, `featureCard`/`controlsFor` (toggle/slider/button/label), «could not read it». Скетч ✗: 4 вигадані перемикачі замість Daily review / Remembering з контролами.
- **Частини**: `settingsFor(pid,b)` — група `settings` (1.11).

### 1.11 Машини і частини (pult + `GET /api/machines`)

| Дані | Старе | Скетч |
|---|---|---|
| `name,alias,pid,state,seen,version,platform,arch,load,disk_free,disk_total,mem_free,mem_total,reported,keyed,brain,desired` | картка: `alias`, «brain», online/away, v/platform/load, «GB free», RAM, «last seen» | ◐ 2 статичні картки |
| `parts[]`: `name,label,channel,on,state,why,why_not,desired,online,pids` | крапка, назва, «×n» при кількох pid, стан (pending/on/off/pending wish/why/cannot), ↻, чекбокс, approve ✓/✗ | ◐ чекбокс без сенсу |
| дії | `PUT /api/machines/{name}`, `…/forget` (двічі), `POST …/parts/{part} {on}`, `…/restart`, `POST /api/update/machine`; клік → `openPart(pid)` | ✗ rename, forget, restart, update, перехід |
| частини: `name,channel,forms,host,version,state,about,hears,approves,skills[effect=change]` | назва, tooltip, «≠» при іншій версії, badges, `health` (up/restarts/trouble), approve/no, `controlsFor`; ⚙ → `settingsFor` (група `settings`) на місці | ◐ карусель; **групи `settings` мусять лишитись** |

### 1.12 Сесії — нові дані для пінів

`GET /api/sessions` → `{active, items[{id,title,earlier,started,updated,unread,turns}]}`.
`DELETE /api/sessions/{id}` видаляє `turns.jsonl` і файли; **новий шар пінів мусить
видаляти піни сесії** (піни — NEW, специфікує архітектор пінів).

## 2. План перестилювання (кроки, кожен — окремий коміт)

1. **Каркас.** `aside.sheet#details` (NEW id) поруч із `#mind`/`#settings`; спільні
   `.sheet/.sheethead/.sheetbody/.sheet.wide/.sheet.mid` зі скетчу; `#veil`, `#settings`, `#mind` лишаються.
2. **Деталі.** Перенести `#page-context` (id мусить лишитись: `drawContext()` пише в нього, тест
   `$("...")` його вимагає) у `#details`; прибрати вкладки Screen/Context, `#live`, `#empty`, `#stage` і
   їхні `$("...")`-звертання, а рендерер артефактів (sandbox, `BRIDGE`, `MAY_GET`) перевести з `#live` на контейнер повідомлення; `openPage` — аліас.
3. **Пам'ять.** Лише CSS/обгортка: `.mind-tabs`→`.mtabs`, картки→`.mfact/.mreminder`;
   `id="mind-tab-*"`, `MIND_TABS`, `mindLoad` не чіпати; `confidence`/`trust` у `.mind-meta` (§5 Q2).
4. **Джерела.** Відкинути модель скетчу (погода/курс/календар); лишити
   `sourcesLoad/sourceImport/sourceUndo`, перестилізовані в `.msource/.source-row` —
   це найбільша втрата скетчу, порт не робити.
5. **Копії.** Додати чекбокс sound, `broken`, `counts`, `restored`, `folder`,
   опитування, `sessionStorage` (усе вже в старому JS) — нові класи.
6. **Профіль.** Три блоки з окремими Save і лімітами з сервера; один Save не приймати
   (сервер пише один ключ за раз).
7. **Налаштування.** `#lang/#provider/#modelpick/#modelcustom/#modelnote/#keylist/#keyname/#keyvalue/#keynote/
   #pair-code*/#features`; `.setrow/.keyrow/.feature`; `featureGroups/featureList`, не 4 вигадані; «Saved…»/«Not saved: {why}».
8. **Деталі.** Лишити `ctxMeasure/ctxTimeline/ctxSheet/promptBits/verbBit`; додати
   «Цей запуск» (`/api/state`) і «Машини» (`/api/machines`) з кнопкою до pult.
9. **i18n і тести** (§3–4), після кожного кроку — `make test`.

## 3. Вплив на тести сторінки (вони читають `page.html` як текст)

- `SCRIPT = re.findall(r"<script>(.*?)</script>", PAGE, re.S)`: лишити **один** інлайн
  `<script>` без атрибутів. `<script src=…>`/`type=module` зробить `SCRIPT=""` — ~20
  перевірок стануть зеленими на порожньому місці; розділення — лише разом із правкою тесту.
- CSS-перевірки ріжуть текст: `.mind-item {` мусить мати `border-radius:12px` і
  `var(--card-2)`; `.mind-item-text {` — `overflow-wrap:anywhere`; `.mind-item-meta {` —
  `var(--mono)`; `"#mind { width:100%; border-left:0; }"` — мобільне правило; CSS не виносити,
  класи не перейменовувати.
- `test_the_mind_drawer_has_journal_and_search_before_profile` фіксує точний порядок
  `id="mind-tab-…"` і `const MIND_TABS = [...]` — зберегти.
- `test_every_element_the_script_reaches_for_is_in_the_page` збирає `$("id")`: нові id —
  статичні в розмітці; `#live` зникає, його `$("...")` переїжджають на вузол повідомлення, а
  `test_a_live_widget_cannot_read_memory_or_say` (`MAY_GET`, `BRIDGE`, sandbox) лишається зеленим.
- `KINDS` (`toggle/slider/choice/label/button/log/text/terminal`): рендерер контролів
  лишається (features, частини); не замінювати на скетчеві `<button class="sw">`.
- `test_no_browser_dialogs…`: жодного `confirm/alert/prompt`; нові підтвердження — `twice()`.
- Літерали, які шукають тести (зберігати дослівно або свідомо правити тест тим самим
  комітом): «The marks stay with the recording», «the journal is empty.», «I recall nothing.»,
  «the search failed», «could not read the journal.», `api("mind/journal?limit=80")`,
  `api("mind/search?q=" + encodeURIComponent(q))`, `sessionStorage.setItem("heare-restoring",
  "1")`, `if (restoring) { mindOpenDrawer(); mindShow("backups"); }`, `api("backups",
  {method: "POST", body: {sound: $("backup-sound").checked}})`, `fetch("/api/backups/bring",
  …)`, `"/restore", {method: "POST"}`.
- **Розмітка vs скрипт.** `heare_babel` бере `data-t` лише з `text.split("<script>",1)[0]`.
  Підписи з JS (скетч будує аркуші рядками) мусять бути `tr("…")` з **подвійними** лапками;
  `data-t` на JS-вузлах не перекладеться (words.py обробляє DOM один раз до скрипта).
- **Ключі.** Нові: `"Details"`, `"Usage"`, `"Timeline"`, `"Bits"`, `"This run"`, `"Messages"`,
  `"Replies"`, `"Uptime"`, `"Cost unknown"`, `"Open machines"`, `"Context"`, `"What is in the prompt"`.
  `"Memory"`, `"Settings"`, `"Sessions"`, `"Machines"`, `"Facts"`, `"Reminders"`, `"Journal"`,
  `"Search"`, `"Hearing"`, `"Profile"`, `"Sources"`, `"Backups"` уже в `heare.po` — не перейменовувати.
- `test_language_rule.py`: українською в `page.html` — лише коментарі, `«…»` і
  «Українська»; кожен новий `tr()` — у каталозі з тими самими `{places}`; `trn()` — три
  українські форми.
- Порядок i18n: маркувати → `make i18n` → перекласти `heare.po` → `make i18n`
  (компіляція) → `make i18n-check`. Без перекладу `i18n-check` падає.

## 4. План тестів

**Дані/API (без мережі).** Наявні вартові в `tests/heare/test_api.py` уже фіксують кожне поле:
`test_the_mind_snapshot_reads_facts_reminders_and_the_profile` (точний dict `counts`, 50
фактів, `reminders`, `review`, `consolidation`, `profile.limits`), confirm/reject/cancel,
`PUT /api/mind/profile`, 503 без сховища, журнал, пошук, `/api/backups`, `/api/heard`,
`/api/context` у переліку маршрутів. Їх не змінювати — вони й доказ «дані не втрачено».

- NEW `tests/heare/test_pins.py`: `POST /api/pins {session,message,at}` →
  `GET /api/pins?session=` → `DELETE`; пін — якір: видалення повідомлення або
  `DELETE /api/sessions/{id}` прибирає піни; `GET /api/sessions/{id}/turns` віддає
  rich-повідомлення (`kind`, `artifact`, `pin`); один живий артефакт на сесію;
  trail/shelf рахуються з `at` і сесії.
- Нові маршрути пінів внести в `REMOTE_WINDOW_ALLOWED` або `LOCAL_ONLY`
  (`test_every_route_is_decided_for_a_remote_window`, `test_the_allowlist_entries_all_match_a_live_route`).
- NEW перевірка деталей: `/api/context` віддає `turns[].messages[].role/content`, `verbs[]`, `limit`, `tasks[].brief`; `/api/state` — `session`, `running`, `model`.

**Текстові тести сторінки.** Оновити `tests/heare/test_page.py` там, де структура свідомо
змінилась (вкладки → `#details`; нові id) і додати: 8 вкладок пам'яті в порядку; `#details`
має `#page-context` і `ctxSheet`; `#settings` має `#lang`, `#modelcustom`, `#features`,
`#keylist`, `#pair-code`; у `SCRIPT` немає `#live/#empty/#stage`; `twice(` є, диалогів немає.

**Мова й залежності.** `test_language_rule.py` без правок після `make i18n`; `test_i18n.py` —
каталог має нові ключі; `make i18n-check`. `test_dependencies.py` — жодних нових імпортів між
частинами; уся робота лишається в `heare/api/`.

**Playwright (NEW `tests/site/test_page_sheets.py`, ручний, як `tests/site/test_panel_setup.py`).**
Статичний сервер над `page.html` із підставленим `/*WORDS*/`; `page.route("**/api/**")` з
фікстурами `mind/context/backups/heard/settings/features/machines`; перевірити: пам'ять —
8 вкладок, Confirm/Reject, журнал, порожній пошук, рядок слуху, збереження профілю,
імпорт/undo джерел, копії; деталі — gauge, тіки, `ctxbit`, задачі; налаштування — мова/
провайдер/модель/ключі/adopt/код пристрою/фічі; один аркуш за раз; 390×844 і 1280×800;
без горизонтального скролу; консоль без помилок; скріншот у `.sisyphus/evidence/`.

**Мобільні.** 390: аркуш на всю ширину (`#mind`, `.sheet`), composer над safe-area, один
аркуш за раз (налаштування закриває пам'ять/ліве меню), цілі ≥ 32 px, стрічка під composer.

```bash
make test                                   # core + door, no network, no keys
make lint
uv run pytest tests/heare/test_page.py -q
uv run pytest tests/heare/test_language_rule.py -q
uv run pytest tests/heare/test_api.py -q
make i18n && make i18n-check
python3 tests/site/test_page_sheets.py      # Playwright, вручну
make live                                   # справжній brain на 127.0.0.1:9790
```
`make live` — фінальна ручна перевірка: пам'ять/деталі/налаштування на живому brain,
числа збігаються з `/api/mind`.

## 5. Критерії приймання і ризики

**Приймання.** (1) кожен рядок §1 має місце й дію; (2) 8 вкладок пам'яті в порядку
facts…backups; (3) `counts`, ліміти профілю, `marked/total`, `counts` копій — з сервера;
(4) джерела — імпортери з undo, не перемикачі погоди; (5) деталі показують реальний
`/api/context` (промпт, розмова, verbs, задачі) і `/api/state` (usage/версія);
(6) налаштування мають мову, провайдера, модель+custom id, ключі з джерелом і adopt, код
пристрою, реальні фічі, контроли частин; (7) жоден endpoint не видалено; (8) `make test`,
`make lint`, `make i18n-check` зелені; (9) page-тести оновлені свідомо, не «позеленіли»;
(10) мобільний 390 без скролу і з одним аркушем.

**Ризики втрати даних.** (1) «Джерела» замінити скетчевою погодою — втрачено імпорт,
прогрес, undo, факти-з-джерела; (2) копії без `counts/broken/sound/restored` — не видно,
що відновлюєш; (3) факти без `confidence` — втрачено видиме сьогодні; (4) слух без
`truth` — втрачено виправлення й навчання; (5) профіль одним Save — сервер пише один
ключ, частина правок зникає; (6) «біти» замість реального промпту — зникає єдиний спосіб
побачити, що модель прочитала; (7) фейкові фічі кличуть не ті endpoint'и — дії тихо не
працюють; (8) показ значення ключа — порушення «Values are never shown»; (9) видалення
сесії без каскаду пінів — якорі на мертві повідомлення; (10) `<script src>`/зовнішній
CSS — тести зеленіють на порожньому тексті; (11) `data-t` у JS — рядки лишаються
англійськими без жодної помилки; (12) втрата `#page-context` — `drawContext()` падає в тиші.

**Відкриті питання.** (1) «Деталі» на мобільному — аркуш чи вкладка? (2) `trust/predicate/
value` лишаємо поза DOM (як сьогодні) чи показуємо в деталях факту? (3) чи прибрати
застарілий `GET /api/memory` разом із `test_memory_is_searchable`? (4) чи «Джерела» скетчу
(погода/курс/календар) — майбутня фіча, і тоді вона в `#features`, не в пам'яті? (5) чи
потрібен новий endpoint usage, чи `/api/state` досить? (6) чи можна перейменувати
`id="mind-*"`, якщо тести переписати тим самим комітом (рекомендація: ні)? (7) де показувати
`review.hour` — сьогодні він у JSON і ніде в DOM.
