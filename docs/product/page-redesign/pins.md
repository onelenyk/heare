# Pins: док закріпленого, чипи й повний перегляд

Специфікація для перебудови `packages/heare-brain/heare/api/page.html`. Читати разом з
`docs/product/page-redesign/` (рештою п'яти специфікацій); цей файл володіє лише pins.
Код не змінений: це план.

## 1. Мета й межа

Закріплене — це **якір, не копія**. Повідомлення живе у своїй сесії; pin лише каже «воно
важливе». Тому втрата чипа не втрачає слів, а видалення сесії видаляє її pins.

Що входить: сховище pins, три маршрути, подія `pin`, док (trail + shelf), чип, повний
перегляд, модельний інструмент `pin`. Що не входить: перетягування чипів, pins у пам'ять
(факти), pins у файли, окрема «дошка». Залежність: pin адресує **повідомлення**, а його
сьогодні не існує як об'єкта. Тому крок 1 міграції — id повідомлень (див. §7).

## 2. Модель pins

Один глобальний файл, бо shelf — це pins інших сесій: `~/.heare/memory/sessions/pins.json`
(поруч із `index.json` і теками сесій; `memory_dir` уже входить у backup).

```json
{"v": 1, "pins": [
  {"id": "9f2c1ab4e7d0", "session": "<32 hex>", "message": "<32 hex>/7",
   "order": 3, "created_at": 1762885402.14, "by": "model",
   "title": "Погода · Бурштин", "line": "+1° · Бурштин", "kind": "weather"}
]}
```

- `id` — `uuid4().hex[:12]`, унікальний у файлі; маршрут `DELETE` приймає лише його.
- `session` — денормалізований, щоб каскад і shelf не розбирали `message`.
- `message` — `f"{session}/{ordinal}"`; `ordinal` — 0-based номер рядка в `turns.jsonl`.
  Лог лише дописується, тому номер стабільний назавжди; **рахувати рядки, не розібрані
  записи** (зламаний рядок не має зсувати всі наступні id).
- `order` — монотонний `max(order)+1` при створенні, для порядку shelf.
- `created_at` — `time.time()`, як `at` у сесіях.
- `by` — `"model"` або `"person"`.
- `title`, `line`, `kind` — необов'язкові; `line` — другий рядок чипа. `kind` — одне з
  `text|html|table|weather|image|audio|video|link|ask|step|game`, підказка для гліфа.

Клас `PinStore` (новий файл `heare/api/pins.py`, лише stdlib):

```
PinStore(path, emit)      # emit = core.state.emit; двері звуть bind_sessions(Sessions)
all() -> list[dict]                # + "missing": true, якщо сесія/рядок не існує
add(session, message, *, by, title="", line="", kind="") -> dict   # ідемпотентно по message
remove(pin_id) -> bool             # drop_session(session) — каскад
resolve(text) -> str | None        # "now" | "latest" | "<id>" | цитата; None — не знайдено
prune() -> int                     # при старті: pins сесій, яких немає
claim_self(session)                # «закріпи те, що зараз скажеш»
bind_self(session, message)        # викликає Kept.keep для assistant
describe(message_row) -> (title, line, kind)   # fallback без моделі
```

`claim_self` ставить запис `{"session": s, "message": "", "self": true, "at": …}`;
`bind_self` підставляє id рядка, який щойно дописав `Sessions.add`, і емітить `pin`.
Запис, який не прив'язався за 120 с (тур упав до `keep`), викидається при наступному
`claim_self`/`all`, у лог — один рядок.

**trail** — pins активної сесії, чиє повідомлення вже **вище** видимої частини стрічки;
сортування за `ordinal` зростанням. **shelf** — pins інших сесій; сортування за `order`
зростанням, у чипі другим рядком префікс короткої назви сесії (`sessionShort`).
Trail і shelf рахує сторінка з одного `GET /api/pins`; сервер не знає про скрол.

Поріг: повідомлення «вище», коли `rect.bottom <= feedRect.top + dockH + 8`, де `dockH` —
висота `#pin-dock` (0, якщо порожній → 12). Зворотний перехід — на `+20` (гістерезис 12 px,
щоб чип не блимав на межі). Міряти раз на кадр у `requestAnimationFrame` зі `scroll`
(passive) і `ResizeObserver` на доці; не `IntersectionObserver` — поріг динамічний.

## 3. API

| Метод | Маршрут | Тіло | Відповідь |
|---|---|---|---|
| GET | `/api/pins` | — | `{"pins": [...], "active": "<session>"}` |
| POST | `/api/pins` | `{"message": "<s>/<n>", "title"?, "line"?, "kind"?}` | `{"pin": {...}}` |
| DELETE | `/api/pins/{id}` | — | `{"ok": true}` / 404 |

- `POST` ідемпотентний по `message`: повторний виклик повертає той самий pin, `order` і
  `created_at` не змінюються, а непорожні `title`/`line`/`kind` оновлюють запис.
- `POST` (з дошки, тому `by` завжди `"person"`) відхиляє: сесію, якої немає (404), рядок,
  якого немає (404), `kind == "ask"` (400 — ask старіє, якір на нього бреше), понад 12 pins
  на сесію або 40 усього (409, текст для дошки).
- Подія: `pin` з `op` = `add|remove`, `pin` — повний запис. Сторінка оновлює док і клас
  `.pinned` без перезавантаження; `remove` несе лише `{"id": …}`.
- Каскад: `session_delete` у `server.py` викликає `sessions.delete(session)` **і одразу**
  `pins.drop_session(session)`, потім `went_to(sessions.active)`.
- При старті `build()` після `sessions.seed(...)` кличе `pins.bind_sessions(sessions)`, далі
  `pins.prune()`.
- Авторизація: усі три — **local-only**, як усі `/api/sessions*`. У
  `tests/heare/test_api.py` додати їх у `LOCAL_ONLY`; у `REMOTE_WINDOW_ALLOWED` — ні.
  Вікно з іншої машини дока не бачить і отримує 403. Причина: pins — це перелік розмов.
- Перезавантаження: `GET /api/pins` один раз при старті, далі події `pin`; після події
  `sessions` — теж перечитати pins (каскад зняв частину, trail залежить від активної сесії).

## 4. Контракт чипа

```
[thumb 34×34] [title (1 рядок, ellipsis)]      [×]
              [line  (1 рядок, mono, soft)]
```

- `title` — з pin, інакше `describe()`: перший рядок тексту (46 символів), заголовок
  артефакта, назва файлу або `"Повідомлення"`.
- `line` — з pin, інакше `describe()`: для shelf — `sessionShort + " · " + line`; для
  trail — `line` без префікса. Порожній `line` показує вид (`"таблиця · 4 рядки"`).
- `thumb` — завжди рахує сторінка з повідомлення: `image` → `<img src=/api/sessions/…/files/…>`
  (файл 404 → гліф); `link` → гліф `link`; `game` → `game`; `audio`/`video` → свої гліфи;
  `html`, `table`, `weather` → `table`/`sun`; `text` → `text`. Гліфи — один `THUMB`
  об'єкт у `page.html`, як `TICO` у начерку; жодного живого вмісту в чипі.
- `shelf` — dashed рамка й прозоріший фон (`.chip.shelf`), як у начерку.
- **unavailable**: `missing: true` (сесія/рядок зникли) → чип `aria-disabled`, гліф `?`,
  title «Повідомлення недоступне»; хрестик працює, перегляд показує решту. Такий pin не
  видаляти мовчки: док показує його, поки людина сама не зніме. Картинка 404 — так само.
- Запис із `self: true` і без `message` у док не потрапляє (ще не прив'язаний).
- Хрестик знімає pin без підтвердження (не руйнівно), з `tr("unpinned")` у тост.

## 5. Повний перегляд

`#pin-preview` — один `position:fixed` вузол над стрічкою (не другий застосунок), як
`.pinpreview` у начерку: шапка `тип + title`, тіло, підвал `Перейти до повідомлення →`.

- Джерело: `msg = messageFor(pin)` з уже завантаженої стрічки; якщо повідомлення немає в
  DOM (інша сесія, `missing`) — перегляд показує `.pv-head` + `title`/`line` + підвал,
  тіло — `tr("Це повідомлення не завантажене.")`.
- Відкриття: hover чипа через 120 мс, закриття через 180 мс; перехід «чип → перегляд» не
  закриває (таймер знімається на `mouseenter`). `focusin` відкриває одразу, `focusout` і
  `Escape` закривають. Позиція: під чипом, фліп над ним, якщо не влазить; на `<900px` —
  фіксовано над композером, `max-height:60vh`.
- **Живе втілення одне**: `liveMount(mid, html, host)` у `page.html` тримає
  `Map<mid, {frame, host}>`. Перегляд забирає втілення: стрічкова копія того самого
  повідомлення замінюється статичним `.live-away` (гліф + `tr("живе у перегляді")`),
  щоб числа не розходились. Закриття повертає втілення в стрічку, лише якщо повідомлення
  в межах екрана; інакше воно лишається розмонтованим.
- Пауза поза екраном (спільний інваріант зі стрічкою): живе втілення розмонтовується,
  коли повідомлення поза `#feed` довше за 800 мс, і монтується при поверненні. Стан
  зберігає сам віджет у `localStorage`/`sessionStorage`, не сторінка.
- Керування у перегляді: `▶/❚❚` для audio/video (лише клас `.playing`, без сервера);
  живе HTML — у тому самому пісочному `iframe` (`sandbox="allow-scripts"`, той самий
  `MAY_GET = /^(system|state|tasks)$/`, без `say` — тест це тримає); посилання — нова
  вкладка `rel="noopener noreferrer"`; `ask` — лише запис, кнопок немає (дозвіл дається
  в стрічці, поки відкритий); `game` — кнопка «відкрити у стрічці» замість другої копії
  канви. Жодних `alert`/`confirm`.
- Перехід: клік по чипу або підвалу → `goToPin(pin)`: та сама сесія — `scrollToMessage`
  (плавно, `+16px`, клас `.flash` на 1.2 с); інша сесія — `POST /api/sessions/{id}/open`,
  `loadSessions()`, потім прокрутка до `data-mid` після `requestAnimationFrame`. Поки
  тур іде, `open` відповідає 409 → тост `tr("Wait for the reply to end.")`, перегляд
  лишається відкритим.
- Перегляд закривається при зміні сесії, скролі дока, відкритті будь-якої шухляди
  (пам'ять/деталі/налаштування/розмови) і `Escape`.

## 6. Модельний бік

Інструмент `pin` живе в `Toolbox` (як `recall`/`remind`): це не `Surface`, бо pin не
приймає відповіді й не має власного голосу.

- `packages/heare-brain/heare/tools/schemas.py` → `_PIN`:
  - `message` (string): `"now"` — відповідь, яку пишеш цим ходом; `"latest"` — останнє
    повідомлення в розмові; `"<session>/<n>"` — id з попередньої відповіді інструмента;
    інакше — кілька слів із повідомлення («таблиця що змінилось»), шукати останнє, що
    їх містить (нормалізовано, ≥4 символи).
  - `title` (string, ≤80), `line` (string, ≤60) — те, що людина побачить у чипі; писати
    мовою людини (це не промпт). `kind` (enum) — підказка гліфа, якщо очевидна.
  - Опис (англійською, у `_PIN`): «Pin a message from this conversation so it stays at
    hand in the dock above the talk. Use it when the person says keep/save/pin this, or
    when you draw something worth returning to. Pin the message, never a copy: the pin
    follows the message and dies with its session. `message` is «now» for the reply you
    are writing, «latest» for the last message, an id from a previous answer, or a few of
    its words. `title` and `line` are what the person reads on the chip, in their
    language; do not pin small talk, apologies, or more than a few things.»
- `packages/heare-brain/heare/tools/pins.py` (новий): `run(store, arguments) -> str` кличе
  `store.resolve(...)` / `store.add(...)` / `claim_self` і повертає речення («Закріпив.» /
  «Не знайшов такого повідомлення.» / «Уже закріплено, 4 у доці.»); для `"now"` —
  «Закріплю, коли скажу.» (bind після `keep`).
- `Toolbox(pins: PinStore | None = None)`; `verbs()` віддає `pin`, коли store прив'язаний до
  сесій; `acknowledging` — ні (відповідь варта бути почутою), `read_only` — ні, `lookup` — так.
- Дозволено закріплювати: повідомлення **поточної** сесії; своє (`now`); чуже за цитатою.
  Не можна: іншу сесію, `ask`/дозвіл, більше 12 на сесію і 40 усього, порожнє повідомлення.
  Спроба — коротка відмова, без винятку.
- Зведення дає **модель у самому виклику** (`title`/`line`), а не скіл: скіл — це третій
  учасник і зайвий round-trip на кожен чип, а док має малюватись за кадр. Коли pin ставить
  людина, зведення рахує `PinStore.describe()` на сервері, тією ж функцією для обох.
- Окремий блок у промпті не додаємо: `Core.system()` скликає лише `context()` outputs, а
  pins не output. Модель дізнається про закріплене з відповіді `pin` (кількість і назви).

## 7. Файли, функції, порядок

| # | Файл | Зміна |
|---|---|---|
| 1 | `heare/api/sessions.py` | `add()` повертає `str` id; `read()` додає `"id"`; `message_id(session, n)`; `at(session, id) -> dict \| None` |
| 2 | `heare/api/page.html` | `data-mid` на кожному рядку; лічильник `turnAt` (скидається в `loadSessions`); `midOf` |
| 3 | `heare/api/pins.py` (новий) | `PinStore`, `describe`, ліміти, `claim_self`/`bind_self` |
| 4 | `heare/api/sessions.py` | `Kept.keep`: після `add` для `assistant` — `pins.bind_self(...)`; `Kept(memory, sessions, pins)` |
| 5 | `heare/brain.py` | `self.pins = PinStore(...)`; `Toolbox(pins=self.pins)`; `serve(..., pins=self.pins)` |
| 6 | `heare/tools/{schemas,pins,toolbox}.py` | `_PIN`, `run`, реєстрація |
| 7 | `heare/api/server.py` | `build(..., pins=None)` (fallback — свій `PinStore`), `bind_sessions`, три маршрути, каскад, `prune` |
| 8 | `heare/api/page.html` | `#pin-dock`, `#pin-preview`, `THUMB`, `loadPins`, `drawDock`, `chipFor`, `openPreview`, `closePreview`, `goToPin`, `setPin`, `liveMount`, `passedDock` |

Порядок випуску (кожен крок окремо зелений): **1–2** (id повідомлень, без UI) → **3–4–5–6**
(сховище й інструмент; док ще не малюється) → **7** (маршрути) → **8** (док і перегляд) →
потім вихід canvas (інша специфікація). Кроки 1–2 мусять вийти до 8.

Міграція: **наявних pins немає** — у `page.html` клас `.pin` це бейдж лічильника на картці
частини, а `desk.pins` у native-hands — апаратні піни OLED; не чіпати ні те, ні те.
`pins.json` створюється при першому записі; старі рядки `turns.jsonl` отримують id позиційно,
тому старий backup не зсуває pins. Блоки canvas мігрує feed-специфікація (вони стають
повідомленнями); доти canvas-блок кнопки pin не має.

## 8. Тести

- `tests/heare/test_pins.py` (новий): id/порядок/`created_at`; ідемпотентність по
  `message`; `remove`; `drop_session` (каскад) і `prune`; запис переживає новий
  `PinStore` на тому ж шляху; `claim_self`/`bind_self` + протермінований self; ліміти
  12/40; `describe()` на text/html/image/link/ask; `all()` позначає `missing`, а не
  викидає; `by == "person"` не може закріпити `ask`.
- `tests/heare/test_api.py`: три маршрути в `LOCAL_ONLY`; 401 без токена; POST→GET→DELETE;
  подія `pin` у `/api/events`; `DELETE /api/sessions/{id}` знімає pins; віддалене вікно —
  403; `POST` на неіснуючий рядок — 404.
- `tests/heare/test_page.py`: `$("pin-dock")`/`$("pin-preview")` існують (`$()`→`id=`); скрипт
  парситься; `data-mid` у `loadSessions`, `turnAt` скидається там само; `openPreview` не
  створює другого `iframe` (один `liveMount`); `Escape` закриває; немає `alert(`/`confirm(`;
  усі нові слова через `tr()`/`data-t`.
- `make i18n`, `make lint`, `make test`. Ручне (`make live`): закріпити з доці і з
  повідомлення, прокрутити, змінити сесію, видалити сесію, перезапустити brain — той самий док.

## 9. Ризики

- **Два власники файлу.** Якщо `PinStore` створять і `brain.py`, і `server.build`,
  останній запис затирає перший. Один творець (`brain.py`); fallback у `build()` — для тестів.
- **Позиційні id.** `read()` сьогодні пропускає зламані рядки; якщо id рахувати по
  розібраних, один зіпсований рядок зсуває всі наступні pins. Рахувати рядки.
- **Втрата каскаду.** Сесія, видалена поза застосунком (restore, ручний `rm`), лишає
  pins; `prune` при старті + фільтр `missing` у `all()`.
- **Дублі.** Подвійний клік, повторний виклик інструмента — один pin на `message`.
- **Підміна «те, що зараз скажеш».** Тур, що впав до `keep`, лишає self-pin без
  повідомлення; TTL 120 с і прибирання при читанні.
- **Розходження живого.** Перегляд і стрічка одночасно з живим віджетом дають різні
  числа; один `liveMount` на `mid`.
- **Дві правди про розмітку.** feed-специфікація теж чіпає `data-mid` і `.pinned`; один
  власник рендера рядка, інакше подвійні атрибути.

## 10. Відкриті питання

1. Чи feed-специфікація вже має `mountLive`/`data-mid`? Якщо так — узяти їх і викинути
   кроки 2 і `liveMount` звідси, лишивши правило «одне втілення».
2. Чи `GET /api/pins` має віддавати й pins сесій, яких немає в `listed()` (архів)? Зараз —
   так, з `missing: true`; якщо архів з'явиться, він віддає свої.
3. Чи потрібен `line` для shelf із назвою сесії, якщо назва сесії вже в тултіпі?
4. Чи можна закріплювати відповідь, яку ще пишеш, і показувати чип до кінця стріму
   (зараз — ні, чип з'являється після `said`), і чи `ask` має бути в доці — зараз ні (§3).

