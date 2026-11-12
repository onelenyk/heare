# Building with the Claude API

Курс: [Building with the Claude API](https://academy.claude.com/courses/building-with-the-claude-api) — Claude Academy, 67 уроків, 8 квізів, ~9 годин.

## Про що / для кого

Повний цикл роботи з Anthropic: від першого `messages.create()` до агентних архітектур. Для software engineer, який інтегрує Claude у продакшн; передумови — Python, JSON, свій API-ключ.

Усі уроки мають текст із кодом.

## API basics

**Accessing the API.** Шлях: клієнт → ваш сервер → API → модель. **Ніколи не звертайтеся до API з клієнтського коду** — ключ витіче. Генерація спиняється з трьох причин: `max_tokens`, end-of-sequence токен, стоп-послідовність. У відповіді — `message`, `usage` і `stop_reason`.

**Making a request.** Мінімум: `client.messages.create(model=..., max_tokens=..., messages=[...])`. Ключ — у `.env` (`ANTHROPIC_API_KEY`), файл — у `.gitignore`. `max_tokens` — запобіжник, не ціль. Повідомлення — словник із `role` (`user`/`assistant`) і `content`; текст — `message.content[0].text`.

**Multi-turn conversations.** API **не зберігає історію** — кожен запит незалежний. Тримайте список повідомлень самі й надсилайте його **повністю** щоразу; інакше «напиши ще одне речення» втрачає сенс. Хелпери: `add_user_message`, `add_assistant_message`, `chat(messages)`.

**System prompts.** Параметр `system` задає роль, тон і обмеження (репетитор дає підказки, а не відповіді). API не приймає `system=None`, тому параметр додають умовно.

**Temperature.** Параметр `temperature` 0.0–1.0 керує розподілом ймовірностей між токенами. Низька (0.0–0.3): факти, код, витяг даних. Середня (0.4–0.7): сумаризація, навчальний контент. Висока (0.8–1.0): брейншторм, креатив.

**Response streaming.** Відповідь іде 10–30 с. З `stream=True` приходять `MessageStart`, `ContentBlockStart`, `ContentBlockDelta` (шматки тексту), `ContentBlockStop`, `MessageDelta`, `MessageStop`. Простіше: `client.messages.stream(...)` + `stream.text_stream`, а `stream.get_final_message()` дає цілісне повідомлення для збереження.

**Structured data.** Claude обгортає JSON у markdown і коментарі. Прийом: **prefill + stop sequence** — `add_assistant_message(messages, "```json")` і `chat(..., stop_sequences=["```"])`; модель дописує лише чистий JSON.

## Prompting

**Being clear and direct.** Перший рядок промпту — найважливіший. Ясність: проста мова. Директива: інструкція, не питання, з дієслова («Write», «Generate»). «What should this person eat?» → «Generate a one-day meal plan for an athlete»; eval зріс 2.32 → 3.92.

**Being specific.** **Output quality guidelines** (довжина, структура, обов'язкові елементи, тон) — майже завжди; **process steps** (пронумеровані кроки міркування) — для складних задач і рішень. Той самий приклад після шести гайдлайнів дав 7.86.

**Structure with XML tags.** Теги розділяють інструкції та дані: `<sales_records>`, `<my_code>`, `<docs>`. Імена — власні й описові; `<data>` гірше за `<sales_records>`.

**Providing examples.** One-shot і multi-shot. Ловлять крайові випадки — як сарказм у sentiment analysis. Найкращі приклади беруть з eval-звіту (оцінка 10), обгортають у `<sample_input>` / `<ideal_output>` і **пояснюють, чому вони ідеальні**.

## Evals

**Prompt evaluation.** Prompt engineering — техніки написання, prompt evaluation — вимірювання. Три шляхи: перевірити раз (зламається в проді), підправити пару кейсів (теж пастка) або прогнати eval-пайплайн і ітерувати за метриками.

**A typical eval workflow.** П'ять кроків: чернетка → датасет → прогін кожного через промпт → grader з оцінкою 1–10 → зміна промпту й повтор. Метрика — середнє: 7.66 → 8.7 після «Answer the question with ample detail».

**Model based grading.** Grader'и — code, model, human. Model grader — ще один виклик API з eval-промптом. Хитрість: окрім `score` просити `strengths`, `weaknesses`, `reasoning` — без цього моделі ставлять «середні» 6. Відповідь дістають через prefill ` ```json` + stop sequence.

**Code based grading.** Перевіряє **format** (лише код, без пояснень) і **valid syntax**. Валідатори повертають 10 або 0: `validate_json` (`json.loads`), `validate_python` (`ast.parse`), `validate_regex` (`re.compile`). Тест-кейс має поле `format`. Фінал — `(model_score + syntax_score) / 2`.

## Tool use

**Introducing tool use.** Без інструментів Claude не знає ні погоди, ні курсів. Цикл: запит з описом інструментів → `tool_use` → ваш код → `tool_result` → відповідь.

**Tool functions.** Інструмент — Python-функція, яку виконує ваш сервер. Описові імена, валідація входу, змістовні помилки: Claude **бачить** текст помилки й може повторити виклик із виправленими аргументами.

**Tool schemas.** Схема — JSON Schema з трьох частин: `name`, `description` (3–4 речення: що робить, коли застосовувати, що повертає) і `input_schema` з `type: "object"`, `properties`, `required`. Схему можна згенерувати самим Claude'ом; патерн — `get_current_datetime_schema`, типізація — `ToolParam`.

**Sending tool results.** Аргументи: `response.content[1].input`, розпаковка `get_current_datetime(**...)`. Блок — `{"type": "tool_result", "tool_use_id": ..., "content": "...", "is_error": False}` — живе **в user-повідомленні**. `tool_use_id` мусить збігатися з `id` відповідного `tool_use`; так Claude зіставляє результати навіть у іншому порядку. У фолоу-апі `tools=[...]` передають **знову**.

**The text edit tool.** Перегляд файлу/директорії, діапазону рядків, заміна, створення, вставка на рядку. Вбудована **лише схема** — реалізацію пишете ви. Версії: `claude-3*` → `{"type": "text_editor_20250124", "name": "str_replace_editor"}`, інакше `{"type": "text_editor_20250728", "name": "str_replace_based_edit_tool"}`.

**The web search tool.** Серверний інструмент, реалізації не потрібно: `{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}`. `max_uses` обмежує пошуки (Claude робить додаткові), `allowed_domains` звужує до надійних джерел (`["nih.gov"]`). У відповіді — `ServerToolUseBlock`, `WebSearchToolResultBlock`, `WebSearchResultBlock` і цитати.

## RAG

**Introducing RAG.** «Запхати 800 сторінок у промпт» погано: ліміт довжини, падіння якості, дорожче. RAG нарізає документ заздалегідь і кладе в промпт лише релевантні чанки. Ціна — препроцесинг, пошук і ризик, що чанк не містить усього контексту.

**Text chunking strategies.** **Size-based** — рівні шматки з `chunk_overlap` (`chunk_size=150`, `chunk_overlap=20`): просто, але ріже слова. **Structure-based** — за структурою (`re.split(r"\n## ", text)`): найчистіші чанки, лише для форматованих документів. **Semantic-based** — групування семантично близьких речень, найдорожче. **Sentence-based** — компроміс (`re.split(r"(?<=[.!?])\s+", text)` + групування й overlap); у проді найчастіше size-based з overlap.

**Text embeddings.** Embedding — вектор чисел −1…+1; що означає кожна вісь, людям не відомо. Anthropic не надає генерацію ембедингів — курс рекомендує **VoyageAI**: `VOYAGE_API_KEY`, `voyageai.Client()`, `voyage-3-large`, `input_type="query"`.

**The full RAG flow.** Чанкування → ембединги → векторна база → ембединг запиту → пошук найближчих → складання промпту. Схожість — **косинусна** (−1…+1; 1 — ідентичність); у базах частіше **cosine distance** = `1 - similarity`.

**BM25 lexical search.** Семантика провалюється на точних термінах: запит про `INC-2023-Q4-011` повертає суміжне, але не те. **BM25 (Best Match 25)**: токенізувати запит → порахувати частоту термів → зважити за рідкістю («a» майже не важить, рідкісний ID — багато; IDF) → повернути документи з найбільшою кількістю високовагових термів. Тому й потрібен **гібридний пошук**: семантика ловить сенс, лексика — точний збіг.

**A multi-index RAG pipeline.** `VectorIndex` і `BM25Index` мають однаковий інтерфейс (`add_document()`, `search()`), тому їх обгортають у `Retriever`. Злиття — **reciprocal rank fusion**: `RRF_score(d) = Σ 1 / (k + rank_i(d))`, `k` зазвичай 60; у прикладі дає 0.833, 0.75, 0.583 — перемагає той, хто добрий в **обох** індексах. Новий індекс реалізує той самий протокол.

## Features

**Extended thinking.** «Чернетка» моделі: у відповіді `thinking`-блок, потім `text`-блок. `thinking={"type": "enabled", "budget_tokens": 1024}`; мінімум 1024, і `max_tokens` **мусить бути більшим** за `budget_tokens`. Дорожче — вмикають за eval. Блок має криптографічний `signature` проти підміни міркувань; буває `redacted_thinking` (зашифрований), який треба вміти повернути в розмову. Несумісний із prefill.

**Citations.** У документ-блоці `"title": "earth.pdf"` і `"citations": {"enabled": True}`. Кожна цитата містить `cited_text`, `document_index`, `document_title`, `start_page_number`, `end_page_number`; для plain text замість сторінок — позиції символів.

**Prompt caching.** Claude робить препроцесинг і без кешу **викидає** його. Кеш не вмикається сам — потрібен ручний breakpoint `cache_control: {"type": "ephemeral"}` на блоці, і блок пишуть у longhand-формі.

**Rules of prompt caching.** Кешується все **до** breakpoint включно; будь-яка зміна до нього (навіть слово «please») інвалідує кеш; breakpoint може охоплювати кілька повідомлень назад; ставити можна на system prompt, інструменти, зображення, tool_use/tool_result; порядок обробки — **tools → system → messages**, тому кешують інструменти й системний промпт передусім; максимум **4 breakpoints**; TTL 5 хвилин, кожне переюзування оновлює таймер безкоштовно, є опція 1 година за вищу ціну запису; мінімум для Claude Haiku 4.5 — **4096 токенів** сумарно (коротший контент не кешується без помилки; видно з `cache_creation_input_tokens`/`cache_read_input_tokens` = 0).

## MCP

**Introducing MCP.** MCP переносить з вашого сервера працю з опису й виконання інструментів на спеціалізовані MCP-сервери. Писати `get_repos`, `search_issues`, `create_file` для GitHub дорого. Автори — будь-хто, часто провайдери. **MCP ≠ tool use**: tool use — механізм виклику, MCP — питання, хто автор інструментів.

**MCP clients.** Клієнт — міст до MCP-сервера, транспорт-агностичний (найчастіше stdio, також HTTP, WebSockets). Повідомлення: `ListToolsRequest`/`ListToolsResult`, `CallToolRequest`/`CallToolResult`. Потік: юзер питає → сервер бере список інструментів → шле питання й `tools` Claude'у → `tool_use` → `CallTool` → результат назад → відповідь.

**Defining tools with MCP.** `FastMCP("DocumentMCP", log_level="ERROR")`; замість ручних схем — `@mcp.tool(name=..., description=...)` + type hints + Pydantic `Field(description=...)`, SDK генерує схему сам. Приклад — `read_doc_contents` і `edit_document` (find-and-replace) з `ValueError` на невідомий `doc_id`.

**Implementing a client.** Клас-обгортка `MCPClient` (керує cleanup) і `ClientSession` із SDK. Методи: `list_tools()` → `result.tools`, `call_tool(tool_name, tool_input)` → `CallToolResult`; у проєкті пишуть або клієнт, або сервер.

**Defining resources.** Ресурси — «GET-хендлери»: віддають дані, а не виконують дії; клієнт шле `ReadResourceRequest` з URI. **Direct** — статичні (`docs://documents`); **templated** — з параметрами (`docs://documents/{doc_id}`), SDK парсить їх у kwargs. `@mcp.resource("docs://documents", mime_type="application/json")`; серіалізацію SDK робить сам.

**Defining prompts.** Готові перевірені шаблони замість запиту користувача. `@mcp.prompt(name="format", description=...)` повертає список повідомлень (`base.UserMessage(...)`); перевіряють через MCP Inspector.

## Agents and workflows

**Agents and workflows.** Workflow — заздалегідь відома послідовність; agent — мета й інструменти, а план моделює Claude. Workflow — коли уявляєте точний флоу; agent — коли не знаєте, яку задачу отримаєте. Приклад «зображення → CAD» — патерн **evaluator-optimizer**: producer (опис → CadQuery → рендер), grader (порівнює з оригіналом), цикл фідбеку, доки не прийме.

**Parallelization workflows.** Один промпт з усіма критеріями змушує модель жонглювати. Рішення: та сама картинка паралельно кілька разів, кожен запит — критерії одного матеріалу (metal, polymer, ceramic, composite), потім агрегатор порівнює й рекомендує.

**Chaining workflows.** Задача ріжеться на послідовні підзадачі (тема → дослідження → сценарій → відео). Головний кейс — довгий промпт з багатьма обмеженнями, де Claude стабільно порушує частину правил. Прийом: крок 1 — генерація, крок 2 — ревізійний промпт («прибери згадки про AI, видали емодзі, перепиши крінж»).

**Routing workflows.** Спершу **категоризація** викликом (список категорій + `<topic>`), потім спеціалізований пайплайн. Жанри відео: Educational, Entertainment, Comedy, Personal vlog, Reviews, Storytelling — кожен зі своїм шаблоном.

**Agents and tools.** Сила агента — комбінування простих інструментів: `get_current_datetime`, `add_duration_to_datetime`, `set_reminder` разом закривають і «котра година», і «нагадай у спортзал». Принцип: інструменти **абстрактні**, не гіперспеціалізовані. Claude Code має `bash`, `read`, `write`, `edit`, `glob`, `grep` — і **не має** «refactor code» чи «install dependencies».

**Environment inspection.** Claude «сліпий»: після кліку в computer use він отримує скріншот, інакше не знає, що сталося. Для файлів — **спершу читати, потім писати**. Для складних агентів інспекцію вписують у system prompt: whisper.cpp для таймстемпів, скріншоти через FFmpeg.

**Workflows vs agents.** Workflows: вища точність, легко тестувати, передбачуваність; мінуси — менша гнучкість, обмежений UX, більше проєктування. Agents: гнучкість, нові ситуації, вміння спитати користувача; мінуси — нижчий відсоток успіху, складне тестування. Рекомендація: **workflow всюди, де можливо, agent — лише коли справді потрібно**.

## Ключові тези

1. API не має пам'яті, а ключ живе лише на сервері: історію тримає ваш код.
2. Tool use — цикл `tools` → `tool_use` → ваш код → `tool_result`, де `tool_use_id` зіставляє запит і результат.
3. Схема інструмента — JSON Schema з `name`, `description`, `input_schema`; якість `description` визначає, чи буде виклик.
4. RAG = чанки + ембединги + векторний пошук + промпт; якість чанкінгу визначає якість системи.
5. BM25 важить рідкісні терми високо, тому гібрид «семантика + лексика» через reciprocal rank fusion перекриває слабкості обох.
6. Prompt caching вмикається вручну через `cache_control: {"type": "ephemeral"}`; зміна до breakpoint інвалідує кеш, порядок — tools → system → messages.
7. MCP не замінює tool use — він знімає з вас написання й супровід схем та функцій.
8. Evals перетворюють «мені здається, стало краще» на число: 2.32 → 3.92 → 7.86.
9. Workflow — типово правильний вибір; agent — виняток для задач, які неможливо передбачити.

## Що варто запам'ятати

- **Порядок роботи над фічею:** промпт → eval-датасет → grader → середня оцінка → зміна промпту → повтор.
- **`max_tokens` — запобіжник, не ціль;** для thinking він мусить перевищувати `budget_tokens`.
- **Абстрактні інструменти перемагають спеціалізовані:** `bash`/`read`/`write`/`edit`/`glob`/`grep` покривають більше, ніж десять вузьких.
- **Інспекція середовища обов'язкова:** спершу прочитати стан, потім діяти.
- **«6 з 10» від model grader — дефолт без контексту;** просіть `strengths`, `weaknesses`, `reasoning`.
- **MCP: `@mcp.tool` для дій, `@mcp.resource` для даних, `@mcp.prompt` для шаблонів.**
