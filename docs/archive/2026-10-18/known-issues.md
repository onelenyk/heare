# Що зламано сьогодні

2026-09-18. Звірено з кодом і запуском 2026-09-18 (гілка `refactor/design-v2`,
`8307b43`; демон не запускався — читання коду, сніпети, тести і `~/.heare/logs/daemon.log`).

Тут лише те, що зламано зараз. Полагоджене сюди не входить; що саме
відпало з попередніх оглядів — у кінці. Кожен пункт: що ламається, як
побачити, де в коді, вага одним словом.

## Безпека

Відкритого нічого. Перевірено: усі маршрути за токеном і перевіркою
`Origin` в одному middleware (`src/api.py:333-361`; винятки лише `GET /`,
`/api/health`, `/favicon.ico`, статика — `src/api.py:46-47`); `/state`
віддає ключі як `bool`, не як рядок (`src/api.py:547-550`); ранжування
пам'яті не інвертоване (`src/memory/sqlite_backend.py:224-228`, «`%%`»
прибрано, коментар над рядком це фіксує).

## Голос

### Мов рівно три, четверта не тримається

- **Що:** `en/uk/ru` вшито в `SUPPORTED_LANGS`, `LANG_TO_VOICE`,
  `WHISPER_NAME_TO_ISO` (`src/voice/language/core.py:3-15`). Польська
  чи німецька відповідь піде англійським голосом, а мову, яку впізнав
  Whisper, викидає фільтр.
- **Як побачити:**
  ```
  uv run python -c "from src.voice.language.core import *; \
  print(detect_language_from_text('Dzień dobry, jak się masz?', fallback='pl'))"
  → en
  ```
  і `detect_language_from_frame` з `language='polish'` повертає fallback,
  а не `pl` (`core.py:70-72`); `pick_voice` віддає `en-US-AriaNeural` для
  будь-якої мови поза трійкою (`src/spine/voicing.py:26`).
- **Вага:** низька — доки мова одна з трьох, нічого не помітно.

## Інструменти

### `web_search` без Serper: шукає, знаходить і каже «нічого не знайшов»

- **Що:** DuckDuckGo сьогодні повертає 200 і десять результатів, але
  розмітка змінилась: `<a rel="nofollow" class="result__a" ...>`. Регекс
  скрейпера чекає `<a class="result__a" href=...>` без атрибутів перед
  `class` (`src/agent/tools/direct.py:834`), збігів нуль, і інструмент
  віддає `success=True, items=[], "No results found"`
  (`direct.py:886-892`). Це та сама форма помилки, що описана в
  коментарі над `direct.py:797-803` для челенджу 9 вересня, — тепер вона
  повернулась через розмітку, а не через 202. Челендж (202) ловиться
  (`direct.py:817-829`), звичайна сторінка — ні.
- **Як побачити:**
  ```
  uv run python -c "import asyncio; from src.agent.tools.direct import _search_duckduckgo; \
  print(asyncio.run(_search_duckduckgo('weather kyiv', None))['items'])"
  → []
  ```
  а той самий POST напряму дає `status 200`, `result__a` ×10; старий
  патерн — 0 збігів, патерн `<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>` — 10.
- **Де:** `src/agent/tools/direct.py:834` (патерн посилання), `:851-853`
  (сніпет так само прив'язаний до старої розмітки).
- **Вага:** висока — кожен пошук через голос відповідає брехнею. `SERPER_API_KEY`
  не задано (`make doctor`), тож цей шлях і є єдиним робочим
  (`direct.py:596-608`).

### Помилка пошуку без причини

- **Що:** на таймауті `httpx` `str(e)` порожній, і інструмент каже
  `"DuckDuckGo search failed: "` — без слова про те, що сталося
  (`src/agent/tools/direct.py:896-901`). Модель бачить «не вдалось» і не
  знає, чи повторювати.
- **Як побачити:** перший виклик сніпета вище на повільній мережі
  повернув саме такий `error`; другий — порожній список.
- **Вага:** низька.

## Дашборд

Зламаного не знайдено. Усі 35 шляхів, які викликає `src/frontend/src/`,
є в роутері `src/api.py` (звірено скриптом по `add_get/add_post`, включно
з `/api/setup/identity/regenerate`, `/api/audio-devices/select`,
`/api/memories/{id}/forget`). Значок Chrome читає живий об'єкт моста, а не
файл чи перемикач (`src/api.py:474-500`); верби браузера ховаються, доки
розширення не спарене (`src/agent/hands.py:84-85`).

## Тести

`uv run pytest tests -q` → **1957 passed, 2 skipped, 9 deselected, 54 с.**
Падінь немає. `--timeout=120` не приймається (немає `pytest-timeout`).

Один тест залежить від порядку:
`tests/test_spine_engine_features.py::test_a_subsystem_that_failed_to_build_reports_off`
проходить у повному прогоні й падає сам
(`uv run pytest tests/test_spine_engine_features.py -k failed_to_build` →
`assert all(e["observed"] …)` хибне). Хтось із сусідів лишає стан, на який
він спирається. Вага: низька, але такий тест нічого не доводить.

`ruff format --check src tests` не проходить (файли не форматовано), а
`make check` цього й не вимагає — лише `ruff check`. Це не поломка, а
факт, щоб не дивуватись діфам.

Фронтенд збирається: `npx vite build` → 53 модулі, 313 мс;
`src/frontend/dist` від 13.09 не старший за жоден файл у `src/frontend/src`.

## Команди й тексти, що відстали від коду

Не ламає роботи, але ламає довіру: людина чи модель роблять, як написано,
і впираються в помилку.

| Що | Як побачити | Де | Вага |
|---|---|---|---|
| `make mcp-list / mcp-status / mcp-enable / mcp-disable / mcp-edit-catalog`, `make reset-session`, `make test-recognizer` викликають підкоманди `src.main mcp`, `reset-session`, `test-recognizer`, яких немає. `make help` їх досі рекламує. | `make mcp-list` → `invalid choice` | `Makefile:121-152`, `src/main.py:351-385` (є лише start, stop, status, provider, audio-input, audio-output, reset-identity, rotate-browser-token, menubar, logs) | середня |
| `make quickstart` шукає `.env` у теці репо й радить туди покласти `GROQ_API_KEY`. Ключі з 13.09 у Keychain, а файл, який читається, — `~/.heare/.env`, не в репо. | `make quickstart` на чистій машині | `deploy/quickstart.sh:33-37`, `src/keys.py:1-27` | низька |
| Вшитий скіл `voice-mode` каже моделі викликати `set_mode` і радить `src.main mode silent`. Ні інструмента, ні підкоманди немає — режими прибрано. `voice-start` і `voice-stop` — актуальні. | `grep -rn set_mode src` → нічого | `skills/voice-mode/SKILL.md:6,12`, `src/main.py:363-372` | низька |
| `set_provider` приймає `openrouter` (перевірка по `PROVIDERS`), але опис інструмента й текст помилки перелічують лише deepseek, zai, opencode. Модель не знає, що openrouter можна. | опис у `TOOLS` | `src/agent/tools/system.py:379-388`, `src/agent/tools/direct.py:1870-1876` | низька |
| `aec_stream_delay_ms = 120` і `aec_noise_suppression` у конфігу **не читаються**: `src/spine/aec.py` зашиває 30 мс. Заміряно ~125 мс на MacBook Pro; 30 мс обмежує подавлення 10–20 дБ. Сам коментар у конфігу це визнає і просить вуха, не коміту. | `grep -n aec_stream_delay_ms src` → лише `config.py` | `src/config.py:145-156`, `src/spine/aec.py:41,76` | середня — це гучність еха, яку чути |
| `search_conversations` є у `VOICE_TOOLS` і в `SCHEMAS`, але не має `ToolDef` у `TOOLS`. Тому `make doctor` рахує «голосу прямо 8», а модель бачить дев'ять. Разом із цим застаріли лічильники в текстах: «five verbs» (`spine/tools.py:1,651`), «three schemas… all sixty-three» (`system.py:49-50`). | `make doctor` vs `SCHEMAS` | `src/agent/tools/system.py:72-84,142-728`, `src/spine/tools.py:408-637` | низька |
| Докстрінг `src/spine/main.py:7-8` каже, що «скелет ще не має ехоподавлення» — має, `SpineAEC` підключено там же на `:185-194`. | прочитати | `src/spine/main.py:5-9` | низька |
| `/api/features` — єдиний важіль дашборда, який не діє наживо: відповідає `restart_required`. Корінний `README.md:66` каже «every control on it reaches the live engine». Одне з двох. | `POST /api/features` | `src/api.py:1214-1215`, `README.md:66` | низька |
| Докстрінги, що описують минулу версію: `src/spine/loop.py:7-11` («напівдуплекс v0, перебивання голосом неможливе» — є двоступеневе), `src/daemon/spine_engine.py:1-9` (`engine = "spine"` / відкат на `pipecat` — такого поля вже немає), `src/daemon/control.py:1-23` (згадує `src/daemon_control.py` і `direct_tools.py`). | прочитати | там само | низька |
| Меню-бар на Stop кладе в State **dict** (`src/menubar.py:240-246`), тоді як State — `dict[str, str]`, демон пише той самий ключ JSON-рядком (`src/daemon/spine_engine.py:1100-1102`), а `/state` робить `json.loads` (`src/api.py:666`). Падіння ловить `try`, тож наслідок — лише «idle» замість чесного стану. | Stop у меню-барі → `GET /state` | `src/menubar.py:240-246` | низька |
| Два різні `forget` під одним ім'ям: голосу — стерти підслухане за N хвилин (`src/spine/tools.py:774-800`), воркеру — видалити спогад за id (`src/agent/tools/system.py:694-706`). Обидва працюють, але промпт і документ, що каже «forget», мусить уточнювати, який. | — | там само | низька |

## Хвости

| Що | Де | Вага |
|---|---|---|
| Три stash від 12.09 (`worktree-agent-*`) не накладаються (`git stash show -p stash@{N} \| git apply --check` падає на всіх трьох), а їхній зміст уже на гілці: `nods` в `tests/e2e/room.py:427`, `mouth` у `src/spine/loop.py`, `remind` у `src/spine/tools.py:439`. Глянути й дропнути. | `git stash list` | низька |
| `tts_voice` — мертве поле: дефолт `en-US-AriaNeural` (`src/config.py:135`), `/api/setup/config` його пише (`src/api.py:1135-1138`), але демон будує тракт із `voice=""` (`src/daemon/spine_engine.py:824`) і голос обирається по тексту відповіді (`src/spine/main.py:150-153`). Читача немає; фронтенд поле не показує. | `grep -rn tts_voice src` | низька |
| Коментар над `/state` каже «this endpoint is unauthenticated» — уже ні, middleware його закриває. Читача вводить в оману. | `src/api.py:544-546` | низька |
| `make stend` пише `docs/stend.html`, якого немає ні в git, ні в `.gitignore`: після кожного запуску дерево брудне. Або ігнорувати, або комітити разом із кодом. | `make stend && git status` | низька |
| Поки `_converse` чекає до 600 с, доки договорить хід не від людини (`src/spine/loop.py:445`), ходи з мікрофона не читаються, і `TurnAssembler` склеює все сказане за цей час в один хід (`src/spine/turn.py:170-201`). Межа, не поломка — сюди, щоб не шукати. | — | — |
| Ніч є лише для судді ініціативи (`src/spine/situation.py:31-32,122-123`): гучність одна (`src/config.py:416`), голос один. Це межа, не поломка — сюди, щоб не шукати. | — | — |

`make doctor` показує `aec ? / mcp ? / telemetry ?`, `hear_all OFF`,
`вимкнено workflow` — усе навмисно: перші три доктор не будує
(`src/doctor.py:62-66`), `hear_all` вимкнено за замовчуванням
(`src/spine/features.py:69`), `workflow` — ярлик без реалізації, знято
свідомо (`src/agent/tools/system.py:571-580`). Це не проблеми.

## Не перевірено

Потребує живого демона; в коді підстав вважати зламаним нема, але й
доказу, що працює, — теж.

- **Вето ініціативи.** Суддя-модель досі може відхилити будь-що, крім
  `deaf` (`src/spine/engine.py:97,658-664`); лікування — приклади в
  промпті (`src/spine/main.py:262-270`). Чи каже «так» достатньо часто —
  видно лише з `heare.db` після дня роботи.
- **Тиша Edge TTS, коли мова запиту ≠ мова відповіді.** По коду голос
  береться зі скрипту тексту відповіді (`src/spine/voicing.py:25-26`),
  кирилиця з fallback `uk` → Остап; російська відповідь на українському
  профілі піде українським голосом, не тишею. Вухом не перевірено.
- **Блок DuckDuckGo на хвилини (202).** Сьогодні прийшло 200; гілка
  челенджу (`direct.py:817`) кодом є, наживо не спрацьовувала.
