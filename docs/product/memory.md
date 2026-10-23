# Памʼять і активність: зони й пакети

*22 жовтня 2026, гілка `feat/memory-foundation`. Це карта відповідальності,
а не перелік усього, що можна зробити. Кожна нова фіча спершу отримує зону,
потім код.*

## Принцип

Core — це **мова розмови**, а не місце для памʼяті, здібностей чи політики.
Усе, що можна винести з core, виноситься; у core лишається тільки те, без
чого розмова перестає бути розмовою.

Пʼять запитань перед кожною фічею:

1. **Чи це змінює саму розмову?** — core.
2. **Що модель бачить на ході?** — `prompt/`.
3. **Що запамʼятовується?** — `memory/`.
4. **Коли говорити або діяти?** — `engine/`.
5. **Що модель може викликати?** — `tools/`.

Якщо відповідь перетинає три зони — спершу протокол, потім реалізація.

## Зони

| Пакет | Володіє | Не повинен |
|---|---|---|
| `heare-core/heare/core/` | `Message`, `Port`, `Output`, `Executor`, `Memory`, `Tools`; ходи, черги, перебивання, тиша, доставка, `State` | імпортувати будь-що, крім stdlib і себе |
| `heare-core/heare/` (листки) | `config`, `laws`, `keys`, `part`, `lifeline`, `pairing` | знати про частини |
| `heare-brain/heare/api/` | двері, реєстр частин, сторінка, канвас | вирішувати розмову |
| `heare-brain/heare/prompt/` | персона, `voice_rules.txt`, бюджет ходу | памʼять, інструменти, мережу |
| `heare-memory/heare/memory/` | `Book`, журнал, профіль, пошук, `heare-memory` CLI | вирішувати, коли говорити |
| `heare-brain/heare/engine/` | `Intent`, `Intents`, нагадування, моменти, доставка | модель, памʼять, HTTP |
| `heare-brain/heare/tools/` | каталог здібностей, схеми, обробники, порти | імпортувати `engine`, `memory`, `api`, `model` |
| `heare-brain/heare/model/` | `Chat`, провайдери, `Dispatcher` | памʼять, двигун |
| `heare-brain/heare/language/` | українські слова й правила | стан, мережу |
| `heare-brain/heare/brain.py` | збирання всього | тримати логіку фічі |
| `heare-voice`, `heare-hands`, `heare-eyes`, `heare-chat`, `heare-keeper`, `heare-reach` | тіла й агенти | імпортувати одне одного |

Правило імпорту не змінилося: **частина бачить core і себе; дві частини
ніколи не імпортують одна одну.** `tools/` тому й отримав порт `Reminders`,
а не імпорт `engine.store`.

## Де що лежить

| Фіча | Зона | Стан |
|---|---|---|
| Профіль власника | `memory/profile.py`, `~/.heare/memory/*.md` | є |
| Закони в промпті | `prompt/persona.py` | є |
| Журнал подій, `hydrate`, `forget`, `export` | `memory/journal.py`, `memory/book.py` | є |
| Один write-path розмови | `memory/book.py` (`Book.keep`) | є |
| Recall за cue | `language/uk.py`, `core/conversation.py` | є |
| Бюджет ходу | `prompt/budget.py` | є |
| Native tool-result цикл | `core/conversation.py`, `model/llm.py` | є для read-only |
| Каталог `recall`/`delegate`/`stop_work` | `tools/` | є |
| Нагадування: створити, показати, скасувати | `tools/reminders.py`, `engine/` | є |
| Порт нагадувань | `tools/ports.py` | є |
| CLI профілю | `memory/cli.py` | є |
| Memory e2e | `tests/heare/test_memory_e2e.py` | є |
| Типізовані факти, провенанс, факти в промпті | `memory/facts.py`, `memory/book.py` | є |
| Review CLI фактів: `facts`, `confirm`, `reject` | `memory/cli.py` | є |
| Консолідація, підсумки | `memory/consolidate.py`, `brain.py` | прохід у мозку що 5 хв, підсумків немає |
| Щоденний AI-огляд памʼяті | `memory/review.py`, `brain.py` | є: 04:00 локально, лише `pending` |
| Якість щоденного огляду (eval) | `scripts/review_eval.py`, `tests/heare/evals/review_cases.jsonl` | є: ручний, платний |
| Справжня компакція | `prompt/` + `memory/` | немає |
| Моменти присутності, бюджет переривань | `engine/` | немає |
| Абсолютний час нагадувань | `tools/reminders.py` | немає |
| Екранна драбина, граф контексту | `eyes/` + `memory/` | немає |
| MCP-конектори | `tools/` | немає |
| Risk engine, per-action approvals | `core/` або `tools/` + `engine/` | немає |
| Контракт рук v2 | `hands/`, `reach/` | немає |

## Куди додавати нову фічу

- **Змінює хід розмови** (перебивання, тиша, доставка, новий вид повідомлення) — core. Але core вже 1288/1350; спершу доведи, що це справді мова розмови, а не політика. Інакше — інʼєкція з brain.
- **Змінює те, що модель бачить** — `prompt/` (персона, бюджет, рецитація).
- **Зберігає або витягує знання** — `memory/` (профіль, журнал, факти, консолідація).
- **Вирішує, коли і як доставляти** — `engine/` (інтенти, моменти, бюджет проактивності).
- **Додає здібність моделі** — `tools/`: схема в `schemas.py`, обробник у своєму модулі, порт у `ports.py`, тест. Ніяких імпортів `engine`/`memory`/`api`/`model`.
- **Додає тіло або канал** — відповідний пакет `heare-voice`/`hands`/`eyes`/`chat`/`keeper`; звʼязок із мозком через двері, не імпортом.

Якщо фіча перетинає зони — спершу протокол у `core/forms.py` або в `tools/ports.py`, потім реалізація. Правило 2: один виклик — прямо; три учасники — межа.

## Контракт фактів (Wave 2)

`Fact` — одне твердження з провенансом: `subject`, `predicate`, `value`,
`text`, `source`, `trust`, `status`, `confidence`, `valid_from/valid_to`,
`source_ids`, `supersedes`, `created_at`.

- `status`: `pending` · `confirmed` · `rejected`; у промпт потрапляють лише `confirmed`.
- `trust`: `user_said` · `user_confirmed` · `model_inferred` · `tool_output` · `legacy`.
- `FactStore.add` **ідемпотентний за `source_ids`**: та сама подія, легасі-рядок або прохід консолідації не створює другий факт.
- Три роботи Wave 2 мають спільний контракт у `memory/contracts.py`:
  **факти** (`facts.py`), **консолідація** (`consolidate.py`), **міграція** (`migrate.py`).
  Вони не редагують контракт; якщо контракту не вистачає — це окреме рішення, не тиха зміна.

## Поточний стан

- Гілка `feat/memory-foundation`, робоче дерево чисте.
- 978 тестів, 2 живі виключені.
- Core — 1288/1350.
- Памʼять: профіль, журнал, відновлення, recall, бюджет, CLI, e2e; підтверджені факти в промпті; `heare-memory facts/confirm/reject/consolidate`.
- Консолідація біжить у мозку кожні 5 хвилин і створює лише `pending` факти; підтвердження лишається ручним (`heare-memory confirm`), промпт не змінюється до нього.
- Щоденний AI-огляд памʼяті (`review_enabled`, `review_hour`; типово увімкнений, 04:00 локально) читає вчорашні `heard` події, пропонує `pending` факти й ніколи не підтверджує їх. Один ручний прохід — `heare-brain review`.
- Якість огляду перевіряє `make eval-review` (`scripts/review_eval.py`): синтетичні випадки з `tests/heare/evals/review_cases.jsonl`, справжня модель і справжні ключі. Це ручний і платний прогін, не частина `make check`; він — якісні ворота для промпту огляду.
- Активність: нагадування: створити, показати, скасувати, абсолютний час; тихі години 22:00–08:00 і одна новина на день; офлайн- і живий текстовий e2e.
- Наступне: Q4 живий факт-цикл, Q5 merge/deploy; потім risk engine, UI фактів, екранний контекст.

## Якість (Q3, 23.10.2026)

- 978 тестів; coverage 80.49%, поріг 80; mypy — 15 файлів `heare-memory`, чисто.
- Hard eval: 20 кейсів × 3 прогони, 18/20 ok, micro recall 1.00, precision 0.92, injection 100%.
- Незалежне ревʼю знайшло 3 high; усі закриті: права БД, stale `_turn`, атомарний `FactStore.add`.
- Відомі обмеження: `identity_change` пропонує і поточну, і колишню професію (review не ставить `valid_from`); `sarcasm_server` нестабільний (2/3, потім 3/3); `forget` ховає хід лише з journal, не з recall; review може пропустити день після рестарту; consolidation перечитує весь журнал кожні 5 хв; дублікати consolidation за `(predicate, value)`; review бере перші 6000 символів дня.

## Звʼязок з іншими планами

`docs/product/clock.md` — про надійність на одному Mac. Цей файл — про
памʼять і активність. Вони не замінюють один одного: спершу те, що робить
щоденне користування можливим, потім те, що робить його розумним.
