# Системне меню (ліва шухляда)

*Специфікація для `packages/heare-brain/heare/api/page.html` (розмітка, CSS, JS) + каталог слів і тести.
Виконується разом із spec чату/пінів, верхньої смуги і шухляд «Пам'ять»/«Налаштування». Нових Python-файлів немає.*

## 0. Межі
**Володіє цим spec:** `#left` (шухляда), `#bodies` (машини), `#bodysec`→`#carousel`/`#dots`/`#bodycount`
(карусель тіла), `#sysfoot` (підвал), `#sessions` (нова шухляда «Розмови»), `#details` (нова «Деталі»).
**Не володіє:** `#feed`, `#composer`, `#rail`, `#mbar`, вміст `#mind` і `#settings`, `#stage`/`#page-context`.
Тому `#hold` (`#ask`/`#intents`/`#tasks`), `#mind-widget`, `#update`, `#stop`/`#confirm`, `#sessionpick`
**переїжджають**, але їхні id і функції лишаються, доки їх не забере відповідний spec (кроки 5–6).
**Правило:** картка машини — життєвий цикл (увімкнено/вимкнено/чекає/чому); картка тіла — те, що частина
каже і чим керує. Один важіль — одне місце.

## 1. Що є сьогодні
- `#left`→`#pult` (hero, State, Memory, «On my mind», Controls) + `#bodies`; на телефоні — шухляда
  (`leftOpen`/`leftClose`/`syncLeftInert`, `#mopenleft`), на широкому — колонка.
- `#partcol` (`partWidget`, `partChip`, `#partfold`) і `#pdrawer` (`settingsFor`, `placeDrawer`, `drawerPid`)
  — окремі поверхні для того самого; їх замінює карусель.
- `#mind` (8 вкладок), `#settings`, `#page-context` (Context) — повні аркуші, лишаються.
- Дані: `GET /api/state` → `bodies`, `session`, `running`, `update`, `model`; `GET /api/machines` →
  `{machines}`; `GET/POST /api/controls`; `POST /api/bodies/{pid}/approve|reject|forget`;
  `POST /api/machines/{name}/parts/{part}` і `/restart`; `PUT /api/machines/{name}`;
  `POST /api/machines/{name}/forget`; `POST /api/update/machine`; `GET /api/sessions` + `/open|/rename|DELETE`.
- Переписуються: `parts()`, `drawFleet()`, `machineCard()`, `machineRow()`, `machineTop()`, `partWidget()`,
  `partChip()`, `partHead()`, `partHealth()`, `placeDrawer()`, `settingsFor()`, `openPart()`, `fold()`,
  `leftOpen()`, `leftClose()`, `syncLeftInert()`, `shut()`, `openSettings()`, `mindOpenDrawer()`,
  `drawContext()`, `showContext()`, `loadSessions()`, `sessionAct`.
- Лишаються: `controlsFor()`, `truth()`, `nameOf()`, `terminalView()`, `typingIn()`, `PART_LABEL`,
  `PART_STATE`, `PART_WHAT`, `FORM`, `lasted()`, `ctxBit`, `ctxTimeline`, `ctxSheet`, `promptBits`, `verbBit`.

## 2. Будова шухляди
```
<aside id="left" aria-label="System" data-t-aria>
  <div id="left-head" class="syshead"><b data-t>System</b><button id="sysclose" aria-label="close">×</button></div>
  <section class="syssec"><div class="seclabel" data-t>Machines</div><div id="bodies"></div></section>
  <section class="syssec bodysec"><div class="seclabel"><span data-t>Body</span><span id="bodycount">1 / 5</span></div>
    <div class="bodycard"><div id="carousel" class="carousel" tabindex="0" role="group" aria-roledescription="carousel"></div>
    <div class="bodyfoot"><div id="dots" class="dots"></div></div></div></section>
  <div id="sysfoot" class="sysfoot">
    <button id="sessionsbtn" class="sessbtn"><span class="sessmain"><b data-t>Sessions</b><small id="sesslabel"></small></span><span class="chev">›</span></button>
    <div class="footrow"><button id="open-mind" class="ghost" aria-expanded="false" data-t>Memory</button>
      <button id="open-details" class="ghost" data-t>Details</button><button id="open-settings" class="ghost" data-t>Settings</button></div>
  </div>
</aside>
```

Нові id: `sysclose`, `bodysec`, `carousel`, `dots`, `bodycount`, `sysfoot`, `sessionsbtn`, `open-details`,
`details`, `details-body`, `details-close`, `sessions-close`, `sessionrows`; класи `.syshead`, `.syssec`, `.bodysec`, `.bodycard`,
`.bodyfoot`, `.slide`, `.ptop`, `.ptile`, `.sname`, `.sstate`, `.sinfo`, `.ctrls`, `.sfoot`, `.mcard`, `.mrows`,
`.sessbtn`, `.footrow`, `.ghost`, `.sr-only`, `.sheet`.
`#left`: `position:fixed; inset:0 auto 0 0; width:min(380px,100%); transform:translateX(-102%);
transition:transform .22s; overflow-y:auto; z-index:30`; `.on`→`transform:none`; `#sysfoot` — `position:sticky;
bottom:0` з фоном. **Прибрати:** `#pult` і всі `.widget`, `.band`, `.partrow`, `.part`, `.pchip`, `#partcol`,
`#partwidgets`, `#partchips`, `#partfold`, `#partfoldbtn`, `#key-asks`, `.pdrawer`, `.pdrawer-top`.

## 3. Оболонка: відкриття, шари, інертність
Стек шарів: `null | "left" | "mind" | "settings" | "details"`; `topLayer()` — верхній.

- `leftOpen()` — **усі ширини**: `.on` на `#left`, `.on` на `#veil`, `#mopenleft aria-expanded="true"`,
  `syncInert()`, фокус на `#sysclose`. Якщо відкритий правий аркуш — на вузькому він закривається, на
  широкому лишається. `leftClose()` — зняти `.on`, повернути фокус на відкривач (`focusStack`), інакше
  `#mopenleft`; якщо правий аркуш відкритий, `#veil` лишається.
- Правий аркуш (`openDetails`, `mindOpenDrawer`, `openSettings`) **не викликає `shut()`/`leftClose()`**.
  Спільний `openSheet(name)`: закриває інші праві аркуші, на вузькому — `leftClose()`; на широкому `#left`
  лишається видимим і робочим. `#settings` переїжджає з лівого краю направо (`right:0;
  border-left:1px solid var(--line); transform:translateX(100%)`), як `#mind`; `#details` — так само.
- `$("veil").onclick = closeTop()`; `Escape` → `closeTop()` (правий аркуш → шухляда → нічого), не `shut()`.
  `shut()` лишається лише як «закрити все» (відновлення після backup).
- `syncInert()`: `#left`, `#mind`, `#settings`, `#details` — `inert = Boolean(top && top !== name)` і
  `aria-hidden` так само; `#chat` — `inert = Boolean(top)`; `#mbar` — `inert = narrow.matches && top === "left"`.
  На вузькому кожен шар — `role="dialog" aria-modal="true"` з пасткою `Tab`/`Shift+Tab`; на широкому `#left`
  — `role="complementary"`, без `aria-modal`, правий аркуш — діалог без пастки (обидва досяжні).
- `narrow` (наявний `matchMedia("(max-width:900px)")`) на зміні: закрити праві аркуші, `leftClose()`,
  `syncInert()`. `z-index`: veil 20, `#left` 30, `#details`/`#mind`/`#settings` 40, `#toast` 80, `#pairing` 90.

## 4. Компактні картки машин
`drawFleet(all)` переписується: `#bodies` = `machineList.map(machineCard)`; без `builtIn()` у шухляді, без
вільного `partRow` і без «Windows and other parts». `machineCard(machine)`: `.mtop` з `.mdot.on` (online) /
`.mdot` (away), `.mname` = `alias || name`, `.mbrain` «brain» за `machine.brain`, `.mstate` (`online`/`away`);
далі `.mrows` — рядок на `machine.parts`. `machineRow(machine, part, entries)`:
- `.mdot` `.warn`, якщо `entries` має `pending`, або `part.state === "unwell"`, або `part.why_not`, або
  `part.desired !== part.on`; інакше `.on` за `part.on`;
- `.pname` = `PART_LABEL[part.channel] || tr(part.label || part.name)`, `×N` якщо `entries.length > 1`;
- `.pstate`: `pending`→`PART_STATE.pending`; `unwell`/`why`→`part.why`; `why_not`→причина;
  `desired !== on`→`tr("pending")`; інакше `on`/`off`;
- `.end`: `pending`→`✓`/`✗` (`partCall(pid,"approve"|"reject",btn)`); інакше `↻` (лише `machine.reported &&
  part.on`, `machineRestart`) і чекбокс `.sw` (`checked = part.on`, `disabled = !machine.reported || why_not`,
  `change`→`machineTurn(machine.name, part.name, checked).then(loadMachines)`);
- `row.onclick` (не на `.end`) → `focusBody(channel)` — прокрутка каруселі й підсвітка слайда (замість `openPart`).

**Прибрати з картки, щоб не дублювати тіло:** `partWidget`/`partChip`/`controlsFor`, `b.about`,
`partHealth`/`lasted`/`trouble`, бейджі `hears === "all"`/`approves`/`skills`, `terminal`/`log`, `mmeta`,
`load`, `disk_free`, `mem_*`, `seen`, `platform`, `arch`, `✎ rename`, `✕ forget`, `Update` — усе це в
`#details`→«Машини» (§6.3); `Update` ще й у банері `#update` (`drawUpdate`). `#mneed` лишається на `#mbar`.
**Живий стан:** `loadMachines()` кожні 15 с (`GET /api/machines`→`machineList`→`drawSystem()`); події
`part`/`control`/`key`/`task`→`refresh()`→`GET /api/state`→`drawSystem()`; `machineTurn`→`POST
/api/machines/{name}/parts/{part}` `{on}`; `machineRestart`→`.../restart`; `machineRename`→`PUT
/api/machines/{name}` `{alias}`; `machineForget`→`POST /api/machines/{name}/forget`. `pending` — це
`part.desired !== part.on`, а не `part.state`. Форма машини: `{pid,name,host,alias,state,seen,version,platform,
arch,load,disk_free,disk_total,mem_free,mem_total,reported,keyed,brain,parts:[{name,label,what,channel,on,state,
why,why_not,online,pids,desired}]}` (`desired` з `machines.view()`).

## 5. Карусель тіла: п'ять частин, один слайд на сторінку
`drawSystem()` = `drawFleet(state.bodies)` + `drawBody()`; `drawBody()` будує рівно п'ять `.slide`:

| # | `data-slide` | хто потрапляє | заголовок |
|---|---|---|---|
| 1 | `voice` | `channel === "voice"` | Voice |
| 2 | `eyes` | `channel === "eyes"` | Eyes |
| 3 | `hands` | `forms.includes("executor")` (opencode і native-hands) | Hands |
| 4 | `page` | `channel === "web"` (усі вікна, `webPid` першим) | Page |
| 5 | `sources` | `channel === "sources"` | Sources |

`SLIDES = ["voice","eyes","hands","page","sources"]` — єдина константа, порядок фіксований. Слайд: `.ptop`
(`.ptile` з іконкою, `.sname`, `.sstate`) + `.sinfo` (`PART_WHAT[channel]`) + `.ctrls` + `.sfoot` (`v…`,
`up {lasted}`, `restarts: n`, `.trouble`). `controlsFor(pid, b)` і `controlsFor(pid, b, "settings")`
вставляються в `.ctrls` **на місці**; окремого `#pdrawer` немає. `Map`-и `cards`/`settingsDrawn` →
`slides`, вузол тримається, якщо `typingIn(node)`, інакше перемальовується лише цей слайд.

**Керування (усе — `POST /api/controls {part: pid, name, value}`, значення з `truth()`):** voice — `mic`
toggle, `speaker` toggle, `volume` slider `0..1 step .05`, `listen` choice (`judge`/`name`), `keep_heard`
toggle (`mode` — head-label); eyes — `looking` toggle (`sees` — head-label); hands cli — `taking` toggle,
`mode` choice, `agent`/`usage` label, `work` head, `terminal` (`terminalView`), `acp` log; група `settings`:
`type`, `binary`, `found`, `model`, `auth`, `auth_env`+`key` (або `login`), `base_url`, `check`, `checks` — у
`<details class="handcfg">` «Виконавець», відкритість у `Set` на pid; native-hands — `work` head, `internet`
toggle, `net_last` label; page — `aloud` toggle **лише для `webPid`**, інші вікна: стан + `Disconnect` (два
натискання, `POST /api/bodies/{pid}/forget`; «Нове вікно» не робимо — маршруту немає); sources — `weather`
toggle, `city` text (`sees` — head-label).

**Недоступні/дозволи:** `pending` → «waiting for a decision», без контролів, рішення в рядку машини (і в
артефакті-питанні); `away` → «not connected», значення сірі, контролі `disabled`; `rejected`/`expired` →
`PART_STATE`; `why_not` → «cannot run here: {why}»; `trouble` → `.trouble`; версія ≠ `state.running.version`
→ `.part-ver` `≠`; `hears === "all"`/`approves`/`skills[*].effect === "change"` → `.badges` у слайді (не
втрачати!). Частини не з п'яти каналів (`text`, невідомі) — не слайд, а рядок у «Деталях»→«Машини».
Порожній слайд: `.sstate` «not connected», `.sinfo` = `PART_WHAT`, `.sfoot` — «its keeper can start it», без
вигаданих кнопок. `#bodycount` = `{i+1} / 5`; `#dots` — 5 кнопок (`aria-label` «Part {n}: {name}»,
`aria-current`); скрол — `scroll-snap-type:x mandatory`, `scrollTo` на `dot`; `ArrowLeft/ArrowRight` на
`#carousel`; `focusBody(channel)` вибирає слайд за `channel`/`forms`.

## 6. Підвал: Розмови, Пам'ять, Деталі, Налаштування
**6.1 `#sessions`.** Новий `<aside id="sessions" aria-hidden="true">` з `.sheet-head` (`#sessions-close`),
`#sessionrows` і `.sr-only` `<select id="sessionpick">` (щоб наявний `loadSessions` не ламався).
`openSessions()`→`GET /api/sessions`→рядки: `title || (earlier ? "Earlier" : "New session")`, `turns`,
`unread`, `pinned`, поточна позначена; клік→`POST /api/sessions/{id}/open`→подія `sessions`→чат перечитує
стрічку. Панель: `#sessionnew` (`POST /api/sessions`), `#sessionname` (inline rename→`POST
/api/sessions/{id}/rename`), `#sessiondrop` (видалити поточну, два натискання). **Видалення сесії видаляє її
піни:** у `.then` після `DELETE /api/sessions/{id}` — `forgetSessionPins(id)`; якщо піни переїдуть у мозок,
`Sessions.delete` скидає їх тим самим комітом. Подія `sessions`: оновити `#sesslabel`, прибрати піни зниклих
сесій. `sessionAct` лишається: `409` («a turn is under way») → `aside(tr("Wait for the reply to end."))`.

**6.2 Пам'ять.** `#mind` не змінюється (8 вкладок, усі маршрути). `#mind-widget` (лічильники `mind-pending`,
`mind-confirmed`, `mind-reminders`, `mind-review`, `#imports-now`) переноситься **всередину `#mind`** під
`.mind-head`; `#open-mind` стає кнопкою підвалу. `mindOpenDrawer()` не викликає `shut()`, а
`openSheet("mind")`; `mindClose()` повертає фокус на `#open-mind`.

**6.3 Деталі.** Новий `<aside id="details">` (правий, `min(620px,100%)`): `#details-body`. `openDetails()` →
`api("context")` + `api("machines")` + останній `state`/`updateState`; `drawDetails()`: 1) **Контекст** — те,
що малює `drawContext()`: `ctxTimeline` (вибір `ctxPicked`), gauge `~tokens`/`limit`, `.fill`/`.legend`,
системний промпт (`promptBits`→`ctxBit` з `sourceOf`), розмова (`ctxSheet`, `fresh`), verbs (`verbBit`);
2) **Руки** — `state.tasks` (`ctxSheet` `#id executor`, `status`, `brief`, час); 3) **Цей запуск** —
`state.session.started/turns/cost_usd/unpriced`, `state.running.version/commit`, `state.model`, uptime
(`lasted`), банер `#update`; 4) **Машини** — усе, що прибрано з карток (`platform`, `arch`, `load`,
`disk_free/total`, `mem_free/total`, `seen`, `keyed`, `brain`, таблиця частин `name/channel/on/state/why/
why_not/desired`, дії `rename`/`forget` (два натискання)/`restart`/`Update`, `builtIn()`); 5) **Що в
промпті** — `.ctxbit`-підсумок блоків. `detailsOpen` + `setInterval(3000, showDetails)` лише поки відкрито
(як `showContext`); `showContext`→`showDetails`; `#page-context`/`.tabs`/`openPage` видаляються разом зі
`#stage`, а `openPage("context")` до того — аліас на `openDetails()`.

**6.4 Налаштування.** `#settings` лишається з усіма полями й id (`lang`, `provider`, `modelpick`,
`modelcustom`, `save-model`, `modelnote`, `keylist`, `keyname`, `keyvalue`, `save-key`, `keynote`,
`pair-code`, `pair-code-box`, `pair-code-value`, `pair-code-copy`, `pair-code-note`, `features`) і
маршрутами `GET/POST /api/settings`, `GET /api/models`, `GET/POST /api/keys`, `POST /api/keys/adopt`,
`POST /api/pair-code`, `GET /api/features`, `POST /api/features/{id}/act`. Змінюється лише бік і
`openSettings()` (без `shut()`).

## 7. Телефон і клавіатура
- `max-width:900px`: `#left` на всю ширину, `z-index:30`; `#details`/`#mind`/`#settings` — на всю ширину;
  рівно один шар: `openSheet()` спершу `leftClose()`, `leftOpen()` закриває аркуші.
- `:focus-visible` — наявне кільце; після закриття шару фокус повертається на відкривач; на телефоні
  `#left` ловить `Tab` (перший/останній `.ghost`), `Escape` закриває верхній шар.
- Карусель: `#carousel tabindex="0"`, `ArrowLeft/ArrowRight`, `Home/End`; `.slide`
  `aria-roledescription="slide"`, `aria-label` «{n} of 5: {name}»; `prefers-reduced-motion` — без smooth.
- Висота: `.carousel { height:clamp(250px,38dvh,340px) }`, слайд `overflow-y:auto`, `.ctrls` — власний скрол;
  `#sysfoot`/аркуші поважають `env(safe-area-inset-bottom)`. Жодних `confirm`/`alert`/`prompt` — лише
  подвійний натиск і `toast`/`aside` (перевіряє тест).

## 8. Тести
`tests/heare/test_page.py` (текстові, без браузера) — додати:
- `test_the_system_menu_has_five_parts_in_order`: `SLIDES = ["voice","eyes","hands","page","sources"]` у
  скрипті та `data-slide="…"` у розмітці в тому ж порядку; `id="carousel"`, `id="dots"`, `id="bodycount"`.
- `test_the_drawer_footer_reaches_every_sheet`: `id="sessionsbtn"`, `id="open-mind"`, `id="open-details"`,
  `id="open-settings"`; `$("open-details").onclick = openDetails`, `$("open-mind").onclick = mindOpenDrawer`,
  `$("open-settings").onclick = openSettings`.
- `test_the_machine_card_does_not_draw_part_controls`: у зрізі `function machineCard`…`function machineRow`
  немає `controlsFor(`; у `function drawSlide`/`drawBody` є `controlsFor(`.
- `test_the_drawer_keeps_the_sheet_open_on_a_wide_screen`: у `openSheet` немає `shut()`; є
  `narrow.matches`+`leftClose()`; `syncInert` згадує `inert`.
- `test_a_deleted_session_takes_its_pins`: у `sessiondrop`/`deleteSession` є `forgetSessionPins(`.
- `test_the_carousel_is_built_from_the_registry`: `state.bodies` і `b.channel` у `drawBody`, без pid руками.
- Наявні тести лишаються зеленими: `$("…")`-id існують, усі 8 `c.kind === "…"` на місці, `alert(`/`confirm(`/
  `prompt(` відсутні, порядок `mind-tab-*` не змінений.
`make i18n` після кожного нового слова; `test_language_rule.py` не пропустить української в `page.html`
(нові слова — лише `data-t`/`data-t-aria`/`tr()`, англійський ключ, переклад у
`packages/heare-core/heare/core/locale/uk/LC_MESSAGES/heare.po`). Наприкінці `make test` і `make live`; руками:
127.0.0.1:9790, ширина 390 і 1440, `Tab`/`Escape`, два шари, видалення сесії з піном.

## 9. Порядок упровадження
1. Шухляда-шар: `#left` на всі ширини, `leftOpen/leftClose/syncInert/closeTop`, veil/`inert`, `#sysfoot`;
   старий `#pult` лишається всередині (схований `.pult-old`), щоб нічого не втратити.
2. Підвал: `#sessions`, `#details` (спершу «Контекст»/«Руки»/«Цей запуск» з `drawContext`), переїзд
   `#mind-widget` у `#mind`, `#update` у «Деталі»; `openSettings`/`mindOpenDrawer` без `shut()`.
3. Машини: новий `machineCard`/`machineRow`, `focusBody`; решта полів — у «Деталях»→«Машини».
4. Карусель: `drawBody`, п'ять слайдів, `controlsFor` inline; видалити `#partcol`, `#pdrawer`,
   `placeDrawer`, `settingsFor`, `partWidget`, `partChip`, `fold`.
5. Переїзд `#hold` у чат/артефакти (spec чату) — лише після того, як стрічка їх малює; `#stop`/`#confirm` —
   у верхню смугу.
6. Прибрати рештки: `#pult`, старий CSS, `vars()` (читає `#stage`→`#chat`/`documentElement`), `show()`
   (`#bigface`/`#nowtext`/`#dot`/`#you`/`#it`), `talkOn`.
7. `make i18n`, тести, `make live`; перевірити телефон і клавіатуру.

## 10. Дані, які не можна втратити
Машина: `pid,name,host,alias,state,seen,version,platform,arch,load,disk_free,disk_total,mem_free,mem_total,
reported,keyed,brain`; частина: `name,label,what,channel,on,state,why,why_not,online,pids,desired`. Реєстр
(`state.bodies`): `channel,forms,about,host,state,machine,version,started,restarts,hears,ui,values,approves,
skills,name`. Сесія: `id,title,earlier,started,updated,unread,turns,active`. Піни — якір (`session + message
id`), не копія; видалення сесії чистить її піни. Пам'ять: 8 вкладок і все, що вони показують (`counts`,
`facts`, `reminders`, `journal`, `search`, `heard`, `profile`, `sources`, `backups`). Деталі:
`turns/messages/verbs/limit`, `tasks`, `session`, `running`, `update`, `model`, машини. Налаштування: мова,
провайдер/модель, ключі та їхнє походження, код пристрою, можливості. Старі дані пульта: лічильники пам'яті,
`#update`, `#hold` (permission/question/tasks), `around`, `session.turns/cost`, `running.version/commit`,
назва й uptime — мають явний новий дім (§6.2–6.3, чат).

## 11. Ризики
- `test_page.py` перевіряє id: розмітку й JS змінювати одним комітом на крок, інакше `$()` впаде.
- `#stage` зникає, а `vars()` робить `getComputedStyle($("stage"))` — без заміни на `#chat` це TypeError.
- `#hold` не можна видаляти раніше, ніж стрічка малює permission/question/tasks: зникне живе питання й кроки.
- На телефоні високий слайд Hands із налаштуваннями + вкладена прокрутка — перевірити 390×844.
- `pending` не зникне, якщо keeper не звітує (`reported === false`): перемикач `disabled`, `mstate` `away`.
- Два місця рішення (рядок машини і feed-артефакт) дають подвійний `approve`; другий — `404`, безпечно.
- `#settings` переїжджає зліва направо — змінюється порядок фокуса й «перший екран» на телефоні.

## 12. Відкриті питання
1. Шухляда на широкому — модальна (veil, `inert` чату) чи ні (клік у чат закриває)? Зараз: veil є, `#left`
   без пастки, правий аркуш її не закриває.
2. Чи має Page-слайд показувати `aloud` інших вікон? Чужі вікна ігнорують чужий `aloud`; зараз — лише стан і
   `Disconnect`. `Нове вікно` з макета: маршруту немає — прибрати чи `window.open(location.href)`?
3. Де живуть піни — у сторінці чи в мозку? Від цього залежить, хто чистить їх при `DELETE /api/sessions/{id}`.
4. Частина `text` (термінальний чат): слайд, рядок у «Деталях» чи не показувати взагалі?
5. `#mind-widget`: переносити лічильники в `#mind-chips` чи лишити блоком під заголовком?
