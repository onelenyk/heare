# Архітектура heare

2026-09-18. Звірено з кодом 2026-09-18 (гілка `refactor/design-v2`).

Що це таке і які є вміння — `overview.md`; що зламано сьогодні —
`known-issues.md`. Тут лише механізм. Кожне твердження має адресу в коді.

## 1. Процеси

Демон, API і фронтенд — **один процес, один asyncio-цикл**.
`python -m src.main start` → `_cmd_start` (`src/main.py:106`): flock на
`~/.heare/heare.pid` (`:129-146`), aiohttp на `127.0.0.1:9780` (`:154-170`),
`State(settings.db_path)`, `API(state, settings)`, і в тому ж циклі
`run_spine_daemon(settings, state, api)` (`src/main.py:99-101`,
`src/daemon/spine_engine.py:750`). SIGINT/SIGTERM ставлять Event `stop`
(`spine_engine.py:769-777`).

| Команда | Що робить |
|---|---|
| `make dev` | `src.main start` у передньому плані |
| `make start/stop/...` | `./hearectl` запускає той самий `src.main start` фоново, pid у `~/.heare/heare.pid` (`hearectl:19,62`) |
| `make menubar`, `Heare.app` | `src/menubar.py`: rumps на головному потоці; окремий потік із власним циклом (`:128-139`) тримає той самий API і конвеєр як task `_run_pipeline` → `_build_and_run_daemon(..., handle_signals=False)` (`:282-307`) |
| `make doctor` | окремий процес, loop без аудіо (`src/doctor.py:79`) |
| `python -m src.spine.main` | хребет без State і API; демон має бути зупинений (`src/spine/main.py:7-8,697-712`) |

`POST /daemon` у menubar керує task-ом у тому ж процесі через hooks
(`src/api.py:991-1008`, `src/menubar.py:163-169`); у CLI — SIGTERM за
pid-файлом, а `start` породжує новий процес
(`src/daemon/watch_controls.py:46-59`).

Фронтенд — React SPA з `src/frontend/dist`, віддається тим самим aiohttp
(`src/main.py:155-160`); опитує `/state`, `/activity`, `/display` раз на
секунду (`src/frontend/src/components/Dashboard.jsx:76,101-103`).

Канали між частинами:

- **State** (`src/state.py`) — словник у пам'яті + таблиця `state`; API
  пише, демон читає поллерами, і навпаки (розділ 7).
- **Файли в `~/.heare/`**: `inject/*.txt` — текст із дашборда (`POST /inject`
  → `src/inject.py:32-46`; демон читає кожні 0,25 с,
  `spine_engine.py:1461-1474`); `voice_state.json` (`src/spine/voice_state.py:26`);
  `audio_{input,output}_device` — назва пристрою, читається раз на старті
  (`spine_engine.py:457-491`); `logs/daemon.log`, `logs/turns.jsonl`
  (телеметрія, `src/spine/telemetry.py`).
- **SQLite `~/.heare/heare.db`** (`src/config.py:184`) — розділ 6.
- **Один Settings** у API і двигуна: `api.config = settings`
  (`spine_engine.py:807`); провайдер/модель/ключ перечитуються на кожному
  виклику через `_live_cfg` (`src/spine/main.py:45-66`) — тому діють без
  рестарту.
- **Підпроцеси і сокети**: MCP-сервери — окрема задача поза boot
  (`spine_engine.py:924-959`); міст до Chrome — WebSocket task
  `browser-bridge` (`:984-996`); TTS — `ffmpeg` (`src/spine/tts.py:3-5`).

## 2. Усередині хребта: задачі і черги

Композиційний корінь — `_build_loop` (`src/spine/main.py:69`); усе
інжектиться в `SpineLoop` (`src/spine/loop.py:92`), loop не імпортує
жодного сусіда. `SpineLoop.run()` (`loop.py:234-258`) створює задачі
зліва; демон додає поллери (`spine_engine.py:1485-1493`).

```
потік sounddevice ──call_soon_threadsafe──▶ audio.input_frames (Queue, 200 кадрів)
                                                     │
spine-listen   (_listen)      aec.process → vad.feed → assembler.speech_started
                                                     │ END: (pcm, starts_seen, hold)
                                                _stt_jobs (Queue)
                                                     │
spine-stt      (_stt_worker)  transcribe → _settle → assembler.transcript
                                                     │
spine-converse (кожні 0,1 с)  _injected (Queue) ─┐ спершу вона,
                              assembler.poll()  ─┘ потім мікрофон
                                                     │ wake → role_flow → create_task
spine-turn     (_answer→respond)  prompt → stream_events → sentences → utt.say
                                                     │
                                    audio._output_buffer ─▶ потік sounddevice ─▶ динамік
                                                     └─▶ aec.push_far (far-end)
spine-engine   (тік 5 с)      notice → judge → loop.inject(..., "initiative")
```

Хто чим володіє:

- **Мікрофон і динамік** — `AudioIO` (`src/spine/audio_io.py:43`), створює
  демон (`spine_engine.py:813-816`). Callback-и sounddevice у своїх потоках,
  не блокують (`audio_io.py:351,401`); кадр 16 кГц/20 мс іде в
  `input_frames` через `call_soon_threadsafe` (`:392-398`), при
  переповненні губиться мовчки.
- **AEC** — `SpineAEC` (`src/spine/aec.py:44`) у `spine-listen`
  (`loop.py:273-274`); far-end живиться з `audio.play()` після перевірки
  mute (`audio_io.py:472-478`), чиститься в `stop_playback` (`:500-501`).
- **VAD** — `EnergyVAD` (`src/spine/vad.py:24`) у `spine-listen` (`loop.py:275`).
- **Wake** — `WakeGate` (`src/spine/wake.py:118`) у `spine-converse`
  (`loop.py:411`); кожна сказана фраза продовжує вікно (`loop.py:720-724`).
- **STT** — один worker `spine-stt` (`loop.py:362`): черга береже порядок,
  навіть якщо мережа поверне відповіді навпаки.
- **LLM і TTS** — у `spine-turn` (`loop.py:563-597,713-733`). Хід завжди
  один: `_turn` (`loop.py:165`).
- **Engine** — `spine-engine`, якщо збудований (`loop.py:241-242`,
  `src/spine/engine.py:303`).

Поллери демона (`spine_engine.py`): `spine-inject-poll` 0,25 с (`:1461`),
`spine-ctl-poll` 0,2 с — mute/gain/cancel зі State (`:1350`),
`spine-aware-poll` 0,5 с — `ears`/`job` (`:1297`), `spine-role-poll` 0,5 с
(`:1261`), `spine-mcp-poll` 1 с (`:1402`), `spine-bridge-poll` 1 с (`:1027`),
`spine-greeting` (`:1517`). Синхронний SQLite іде через `asyncio.to_thread`
(`loop.py:487,582,731`).

## 3. Шлях одного ходу

1. **Кадр.** 16 кГц mono int16, 640 байт (`audio_io.py:46-48,258-265`);
   `input_gain` у callback-у (`:383-385`); під mute кадри викидаються (`:379`).
   Після mute `take_input_gap` → `vad.drop_in_flight` (`loop.py:269-272`).
2. **VAD.** START після 120 мс гучніше за −38 dBFS, END після `stop_ms`
   тиші (демон ставить `spine_vad_stop_ms`, типово 800,
   `src/spine/main.py:157-159`), preroll 300 мс, максимум 30 с
   (`vad.py:45-55,117-204`).
3. **START** → `assembler.speech_started()` (`src/spine/turn.py:154`); якщо
   динамік грає — розділ 5. **END** → `_stt_jobs` (`loop.py:307`).
4. **STT** (`main.py:128-144`): коротше за 240 мс гучного — порожньо без
   запиту; `usage.stt`; POST на Groq `whisper-large-v3`
   (`src/spine/stt.py:46,102`); фільтр галюцинацій `is_junk` з вікном 25 с
   після власної репліки (`main.py:24,138-143`). Демон обгортає це записом
   `voice_state` stt→result→listening (`spine_engine.py:1122-1145`). Далі
   `_settle` і `assembler.transcript(text, speech_resumed=...)`
   (`loop.py:382-386`).
5. **Збирання ходу.** `TurnAssembler` (`turn.py:76`): один дедлайн —
   `spine_turn_hold_seconds` (1,3 с, `main.py:661-663`) або вдвічі довший,
   коли фраза звучить недоказаною (`turn.py:55,196-201`); сон машини скидає
   накопичене (`:123-152`); мертвий STT закривається через 10×hold (`:221-232`).
6. **Converse.** `assembler.poll()` → wake-гейт, окрім ролі в сесії та
   інжектованого (`loop.py:409-427`); відхилене пишеться як overheard лише
   при `hear_all` (`:467-480`); `role_flow.handle` може з'їсти хід
   (`src/spine/role_flow.py:122`); далі `create_task` (`loop.py:446`).
7. **respond** (`loop.py:517`): `engine.observe_reply` (`:525`);
   `persist.log_user_turn` → `turn_id` (`:530`); промпт з `_make_prompt` —
   персона, роль, MCP-блок, 3 спогади, 4 останні обміни, ситуація двигуна
   (`main.py:561-622`); в модель іде system + 12 останніх реплік
   (`loop.py:38,546-549`). Utterance відкривається **до** стріму, щоб у
   напівдуплексі mute покрив і думання, і інструменти (`:554-561`).
8. **Модель.** `stream_events` зі схемами верб (`src/spine/llm.py:97`,
   `main.py:447-449`): дельти, tool_call-и, usage; стрім рветься, щойно
   `utt.stopped` (`loop.py:567-568`).
9. **Речення → голос.** `sentences()` (`src/spine/sentences.py:4`) ріже
   дельти; `_speak` (`loop.py:713`): `last_spoke_ts`, `wake.spoke()`,
   `utt.say()` → edge-tts MP3 → ffmpeg → PCM 24 кГц (`tts.py:1-7,29`) →
   `speaker.play(chunk)` по мірі надходження (`src/mouth/utterance.py:86-92`).
   Демон обгортає `_speak` записом `agent_state` (`spine_engine.py:1190-1201`).
10. **Верби** після тексту: `_run_tool` → `toolbox.execute`
    (`loop.py:615-622,735-748`); дев'ять схем у `src/spine/tools.py:412-628`.
    Результат `delegate` повертається через `loop.inject(text, source="hands")`
    (`main.py:338-339`, `src/agent/hands.py:197`).
11. **Кінець.** `utt.finish(timeout=60)` чекає, поки динамік спорожніє
    (`utterance.py:153-173`); `close()` віддає мікрофон (`:181-182`).
    Збій — `FAILURE_LINE` через `_say_now` (`loop.py:33,655-663`);
    `persist.log_agent_reply` (`:664-668`); `role_flow.note_exchange` (`:676-678`).

## 4. Дві смуги

Хід має джерело. `PERSON_SOURCES = {"mic", "dashboard"}` (`loop.py:44`) —
людина. Решта чекає: `hands` (`main.py:339`), `initiative[:<kind>]`
(двигун, `main.py:534`), утримані репліки ролі (`role_flow.py:249-267`).
Мікрофон приходить через `assembler.poll()`, усе інше — через
`loop.inject(text, source)` у чергу `_injected` (`loop.py:390-396`).
`_converse` спершу дивиться в `_injected`, потім в assembler
(`:402-406`); інжектоване минає wake-гейт (`gated = False`).

Коли хід уже відповідається (`loop.py:435-446`):

```
від людини → stop_speaking("barged"); _let_finish(running, 5 с)
від решти  → _let_finish(running, 600 с) — нічого не зупиняє
```

`_let_finish` (`:456-465`) чекає під `shield`, щоб зупинений хід встиг
записати себе в базу; хто не вклався — `cancel()`. «Решта чекає» означає:
рядок лежить у `_injected`, а `_converse` стоїть у `_let_finish` до
10 хвилин, поки відповідь не закінчиться сама. Поки він стоїть, ходи з
мікрофона теж не читаються — assembler тримає їх до наступного `poll()`.

## 5. Перебивання: два ступені

Умови: повний дуплекс (`_duplex` = AEC зібрався й активний,
`loop.py:196-198`), `barge_in_enabled` (перемикач `interrupt_off`,
`spine_engine.py:1365-1370`), зібрані `judge_interruption` і `Mouth`
(`main.py:170-173`). У напівдуплексі мікрофон під час відповіді глухий
(`utterance.py:59-63`) — голосом не перебити, лише Cancel з дашборда
(`cancel` у State → `stop_speaking("cancelled")` + `toolbox.cancel_all()`,
`spine_engine.py:1391-1396`) або текстом. Без судді чи Рота — старий
barge-in: `stop_speaking("barged")` одразу на START (`loop.py:317-322`).

```
VAD START, динамік грає
   ▼ ступінь 1 (loop.py:309-328) — тексту ще нема
mouth.pause_all() — динамік мовчить одразу; _hold = _Hold()
   │ listen лічить vad.loud_ms_in_speech → hold.heard_ms (loop.py:276-288)
   │ ≥ 600 мс (INTERRUPT_HOLD_MS, src/text/backchannel.py:26) → hold.long
   ▼ ступінь 2 (_settle, loop.py:330-360) — прийшов транскрипт
hold.long: порожньо → resume (кашель), інакше → interrupt
коротко:   decide(text, speech_ms) (backchannel.py:149-179)
           «ага/ок/так» → resume · «стоп/ні/досить» → cancel
           порожньо → resume · будь-що інше → interrupt
```

Що робить Рот (`src/mouth/utterance.py`):

- `pause()` (`:104-127`) скидає чергу динаміка через `stop_playback`
  (з нею far-end) і за відданими байтами запам'ятовує, **яке речення**
  звучало. `resume()` (`:129-140`) програє його з початку, потім решту;
  для цього PCM кожного речення тримається до `close()` (`:51`).
  Транскрипт «ага» ходом не стає (`loop.py:382-383`).
- `stop(outcome)` (`:142-149`): `stopped`, `outcome` ∈ {barged, cancelled,
  wedged, done}; далі `say()` не синтезує (`:76`), `_deltas` перестає платити
  за стрім (`loop.py:567`), tool_call-и пропускаються (`:616-617`).
- `Mouth` тримає список живих utterance (може бути два: закривальна фраза
  ролі поверх відповіді) з `pause_all/resume_all/stop_all` (`:187-236`);
  `interrupted` описує лише останній — клямки немає.

Збій STT під час hold → resume (`loop.py:375`). Після interrupt/cancel
текст стає ходом і далі діє смуга людини з розділу 4.

## 6. Сховище

Одна база `~/.heare/heare.db`, WAL. Спільна схема — `SCHEMA` у
`src/store/storage.py:19-114` (`SCHEMA_VERSION = 8`): `meta`, `transcripts`,
`displays`, `actions`, `conversations`, `turns`, `user_profile`,
`usage_events`. Її виконує кожен, хто відкриває файл (`storage.py:180`,
`src/spine/persist.py:140`, `src/spine/tools.py:362`); ALTER-и — у коді
(`storage.py:187-278`, `state.py:121-131`). `migrations/*.sql` — історія;
ніщо в `src/` їх не виконує.

Решта таблиць — `CREATE TABLE IF NOT EXISTS`, версію не чіпають:
`transcripts_fts` + тригери (`storage.py:142-163`, створює `persist.py:184`),
`role_sessions` (`persist.py:106-119`), `intents` (`src/spine/intents.py:40-58`),
`observations` (`src/spine/repeats.py:68-76`), `jobs` (`src/agent/jobs.py:36-49`),
`memories` + fts (`src/memory/sqlite_backend.py:72-95`), `state` (`state.py:27-29`).

| Таблиця | Хто пише | Хто читає |
|---|---|---|
| `transcripts`, `turns`, `conversations` | `SpinePersistence` (свій sync `sqlite3`, `persist.py:134`): `log_user_turn` `:337`, `log_agent_reply` `:363`, `log_overheard` `:410`, `forget_overheard*` `:432,452`; `close_idle_conversation`/`save_summary` `:542,601` з тіка двигуна (`engine.py:396-436`); тихі канали ролі через `persist_turn` (`role_flow.py:448`) | промпт (`main.py:562`), верба `search_conversations` (`persist.py:207`), `Repeats` (`:612`), API `/activity` — UNION transcripts ∪ actions ∪ conversations (`src/api.py:775-794`), `/state.last_response` (`:703-709`) |
| `role_sessions` | `RoleManager` через persist (`src/spine/role_session.py:106-126,206-219`) | демон на старті закриває живу як `interrupted` (`spine_engine.py:697-747`) |
| `jobs`, `actions` | `records` — aiosqlite (`tools.py:339-376`): `JobStore`, `SpineActionLog` → `upsert_action_log_entry` (`storage.py:352`); пише виконавець | `engine._notice` (`engine.py:605-636`), `spine-aware-poll` → State `job` (`spine_engine.py:1324-1344`), `/activity` |
| `intents` | `IntentStore` над `records.db`: `engine.notice/mark_voiced/settle/drop` (`engine.py:591,662,671,737`), верба `remind` (`main.py:426-434`), API drop (`api.py:1990-1994`) | тік і `prompt_block` (`engine.py:759`), API `/api/intents` (`:1941`; `api._intents` = той самий store, `spine_engine.py:863`) |
| `observations` | `Repeats` (`repeats.py:326,475`) | `Repeats` |
| `usage_events` | `SpineUsage` — sync `sqlite3` (`src/features/usage/ledger.py:43,107,154,201`) з `usage.llm/stt/tts` у loop | `/state.usage` через `fetch_usage` (`api.py:645`, `src/dashboard_data.py:102`) |
| `memories` | верба `remember` (`tools.py:906`), API forget (`api.py:1934`) | промпт `memory.context` (`main.py:570-573`), `/api/memories` (`api.py:1904`; бекенд хребта, `spine_engine.py:861`) |
| `displays` | інструменти виконавця `show_canvas`/clear (`src/agent/tools/direct.py:2036,2096`), `clear_display` (`persist.py:502`) | `/display` через `state.get_latest_canvas()` (`api.py:980`, `state.py:133`), верба `read_display` (`persist.py:483`) |
| `state` | `State.set/set_bulk` (розділ 7) | `State.init` (`state.py:23-33`) |
| `user_profile` | `src/store/user_profile.py` з `src/agent/tools/direct.py` | там само |

Три двері в один файл: aiosqlite (`records`, `memories`, `State`, API),
sync `sqlite3` у `to_thread` (`SpinePersistence`, `SpineUsage`) і
`TranscriptStore` (`storage.py:166`), який на хребті лише читає (`:126-130`).
Поза базою: `turns.jsonl`, `workspace/artifacts` (`main.py:360`).

## 7. State

`src/state.py`: `_cache: dict[str, str]` + таблиця `state`. `get/get_bool/
get_int` синхронні (`:40-50`); `set/set_bool/set_bulk` — async, у кеш і в
базу (`:54-83`); `set_cache_only` — лише кеш, для частого й неважливого
після рестарту (`:67-73`). `snapshot()` — це `GET /state` (`:87-89`;
значення ключів API замінює на bool). Один екземпляр на процес
(`src/main.py:162`, `src/menubar.py:159`), у loop — як `loop.state`
(`src/spine/main.py:177`).

Пише в базу (`set`): API — `mute_mic`/`mute_bot` (`api.py:872`),
`interrupt_off` (`:888`), `provider` (`:913`), `model_<provider>` (`:926`),
`cancel="1"` (`:970`), `bridge_reload="1"` (`:1534`), будь-що через
`POST /state` (`:747`), зокрема `mcp_reload`. Демон — `running`
(`spine_engine.py:795,1480,1554`) і скидає в `"0"` виконані `cancel`,
`bridge_reload`, `mcp_reload` (`:1044,1392,1431`). Menubar на Stop —
`running`, `voice_state`, `agent_state` (`src/menubar.py:239-246`).

Пише лише в кеш (`set_cache_only`), усе в `spine_engine.py`, окрім останнього:
`boot_status` (`:114-150`), `spine_features` (`:440-454`), `mcp_status`
(`:565-578`), `role_interrupted` (`:738`), `hands_progress` (`:894`),
`roles_available` (`:1246`), `role_hint` (`:1251`); `voice_state` — на кожній
стадії STT (`:1097-1104`); `agent_state` — на кожному реченні (`:1106-1116`);
`ears`, `job` і `role_*` — кожні 0,5 с, лише при зміні (`:1261-1346`);
`engine_verdict` — двигун, кожен тік 5 с (`engine.py:328-344`).

Читає: дашборд — `GET /state` раз на секунду (`Dashboard.jsx:76`) і раз на
2 с (`App.jsx:112-119`); `spine-ctl-poll` кожні 0,2 с — `mute_mic`,
`mute_bot`, `interrupt_off`, `input_gain`, `output_volume`, `cancel` →
`AudioIO` і `loop` (`spine_engine.py:1363-1396`), і тільки це робить кнопки
дашборда живими; `spine-bridge-poll` і `spine-mcp-poll` — `bridge_reload`,
`mcp_reload` раз на секунду (`:1042,1441`); двигун — `agent_state` у
`situation.observe` (`src/spine/situation.py:245`), так `judge` знає, що
асистент говорить (`engine.py:169`); API `/inject` — `running`, щоб чесно
сказати, чи є кому прочитати файл (`api.py:1084-1089`).
