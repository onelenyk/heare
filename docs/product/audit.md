# heare — синтез девʼяти аудитів (2026-10-29)

Один документ, що зводить `01-core` … `09-tests`. Дублі злиті; кожен рядок
посилається на звіт і на `file:line`, які вже наведені в тому звіті. Нового
доказу тут немає. Severity не знижена: де звіт казав P0 — лишилося P0; єдине
підвищення (P0-02) позначене й обґрунтоване в самому рядку.

База: 1 471 тест проходить, 84.6–84.7 % покриття, `ruff` чистий.

Девʼять сирих звітів (з доказами й повними списками) лежать у
`.sisyphus/research/2026-10-29-audit/` — це робочі нотатки сесії, вони не
комітяться. Тут — те, що з них вижило після злиття дублів і за чим можна
працювати. Аудит — знімок 29 жовтня, не живий стан: після кожного кроку
таблиці нижче треба звіряти з кодом.

---

## Резюме

Застосунок **стабільний у щасливому шляху й нестабільний на швах**: ламається не розмова, а перехід між процесами — рестарт мозку, повторний стук частини, забуття, глухий мікрофон, два keeper-и. Планка «тиждень без термінала» (Г7, `clock.md`) **не виконана**: 05, 06 і 07 незалежно описують стани, з яких людина сама не вибереться.
Пʼять речей до тижня щоденного використання:
1. **Двері.** `machine@` стає будь-якою частиною (`server.py:527-535,559-572`; 02 §2.1), вікно має права людини й підміняє `update_feed` (`server.py:345-352`; 02 §2.4), форма відкрита DNS-rebinding-у (`setup.py:83-94`; 07 F-10) — це RCE й крадіжка ідентичності.
2. **Забуття.** Забуті слова лишаються фактами в промпті, їх переучують платні проходи, вони в `turns.aggregated_text`, журналі, експорті й бекапах (04 P0-1…P0-3).
3. **Голос.** Вотчдог глухоти — мертвий код (`hearing.py`; `device.py:181-200,302-358`), а рестарт мозку посеред відповіді лишає мікрофон вимкненим назавжди (05 P0-1/P0-2).
4. **Руки.** Агент успадковує весь `os.environ` і весь FS (`runner.py:242`; 06 H-01), «ніколи» не діє для Claude й обходиться для opencode (`agents.py:140-179,256-261`; H-02), «стоп» не зупиняє команду (H-05).
5. **Життя.** Два keeper-и одночасно (`keeper.py:66-104`, `install.sh:537`, `App.swift:48`; 07 F-1/F-50), SIGKILL keeper-а осирочує частини (F-2), `body → all` лишає `machine_token` і всі частини ловлять 401 вічно (F-3).
Шостий, дешевий: **перемикач мови бреше** — сторінка й голос міняються, а `Core.words`/`Core.judge` зафіксовані в `Brain.__init__` (`brain.py:147`), тож «стоп»/«так»/«ні» не працюють до рестарту (03 §7; 08 §3.1/§3.2).

---

## Стан по частинах

| Частина | Контракт | Ізоляція | Якість | Головний ризик | P0 / P1 |
|---|---|---|---|---|---|
| **ядро** (`heare-core`) | ок; `Port.sounded` і `Desk.question` не в протоколі, `Executor.run` не вміє `failed` (01 §Contract) | `heare/core` чистий; дірка: assembly-виняток і relative imports (01 §Isolation; 09 §10) | 91.0 % core, 0 `Any`, ruff чистий; `_exchange` 99 рядків, 3 публічні параметри без типів (01 §Code quality) | рестарт мозку втрачає роботу/дозволи/питання; частина не дізнається (01 P0-1) | **1 / 6** |
| **двері мозку** (`api/`) | ок: усі 67 маршрутів під `guard` (02 §1) | **порушення:** `machine@` може стати будь-якою частиною; вікно = людина; `approves` недокументований (02 §2) | server 85.1 %, canvas 84.4 %, skills/screens 100 %; blocking token-IO на кожен запит (02 §7) | ескалація ключа машини до виконавця й читання всієї розмови (02 §2.1) | **2 / 5** (+сторінка 1) |
| **розум** (engine/tools/prompt/model) | ок, але `kind` і `effect` довіряються частині; `laws.RISKY` мертвий (03 §3.3, §4.3) | ок; результати роботи входять у промпт і до судді як слова власника (03 §3.2) | brain.py 46.4 %, chat 39.1 %, providers 38.6 %; багато мертвих констант (03 §6/§9) | тиха порожня відповідь і результати, які суддя читає як «власник сказав» (03 §6.3, §4.3) | **1 / 8** |
| **памʼять** | ок; один `heare.db` має трьох власників (04 §1.1) | ок (part імпортує лише core); три стилі зʼєднання, жодного `busy_timeout` (04 §2.2) | 82.5 % пакета; persist 55.5 %, storage 64.1 %, половина — мертвий код (04 §6.6) | забуття не забуває; втрата ходу між журналом і SQLite (04 P0-1…3, P1-1) | **3 / 9** (+2 у таблиці мертвого) |
| **голос** | ок; `Voice.abilities`/`speaking` не використані (05 §5.6) | ок; sync `httpx` всередині аудіо-петлі (05 §5.1) | channel 45.9 %, voice 78.6 %, tts 71.6 %; DSP-частини 96–99 % (05 §8) | глухий мікрофон без відновлення; мікрофон залишається вимкненим після рестарту (05 P0-1/P0-2) | **2 / 7** |
| **руки** (reach + cli-hands) | ок; «ніколи» — кооперативна конфігурація, не межа (06 H-02) | **порушення:** весь env і весь FS, `cwd` навіть не переданий процесу (06 H-01) | acp 81.6 %, cli_hands 47.5 %, runner 92.7 % (06 §Вступ) | агент бачить ключі й `~/.heare`, а «стоп» не вбиває команду (06 H-01, H-05) | **3 / 3** |
| **нативні руки** | ок; model-клієнт `timeout=None`, фінальний хід поза бюджетом (06 H-07) | ок: лише HTTP до мозку й моделі (06 §4) | native_hands 88 %, sim 78.3 %; деградація чесна (06 §3) | дубль імені бере `effect` першої частини → `change` без судді (06 H-08) | **0 / 3** |
| **тіла** (eyes/sources) | ок; `surroundings` = app/present/playing (07 §1.6) | ок; локація йде до Open-Meteo, не задокументовано (07 F-32) | sense 21.7 %, eyes 71.2 %, sources 75 % (07 §Вступ) | «який застосунок спереду» не працює ніколи; нотатки губляться при рестарті (07 F-7/F-8) | **0 / 7** |
| **життя** (keeper/app/install/update) | **порушення:** два годинники — app і `install.sh`/launchd (07 F-1/F-50) | **порушення:** немає single-instance lock, немає process-group kill (07 F-1/F-2) | keeper 66.4 %, setup 78.7 %, chat 33.9 % (07 §Вступ) | два keeper-и → дві частини; SIGKILL keeper-а → сироти; `machine_token` → 401-петля (07 F-1/F-2/F-3) | **3 / 16** |
| **веб-сторінка** | ок; 9 `innerHTML`, лише canvas-шлях (02 §6) | **порушення:** віджет і `screens` — код у origin людини (02 §6) | 2585 рядків, 73 `data-t` + 284 `tr()`; жодного runtime-тесту (08 §1.2; 09 §5) | сторінка не перестукується після рестарту; віджет читає історію й «говорить» як людина (02 §3/§6) | **0 / 1** |
| **тести** | — | дірка: relative/dynamic imports тікають; page-тести не запускають сторінку (09 §10/§5) | 1471 проходять, 84.6 %; 5 зон фальшивої впевненості (09 §Вступ) | мертвий `laws.risky` тестується як «ворота»; фейки тестують фейки (09 Vacuum §1, §3) | **0 / 0** |

> Лічильники — за звітами; консолідовані списки нижче зливають дублі, тому
> рядків у них менше, ніж сума.

---

## Консолідований список P0

| ID | Назва | Що відчуває людина | Доказ | Область | Орієнтовний фікс | Тест-спершу |
|---|---|---|---|---|---|---|
| **P0-01** | Ключ машини стає будь-якою частиною | Чужа «машина» тихо забирає роботу агента, читає всю розмову, смикає тіла | `server.py:527-535` (пропуск `who != pid` для `machine@`), `559-572`, `508-516`; `registry.py:110-111`; `test_api.py:912` — лише happy path | двері | Для `machine@` примусово `pid` = власний pid машини; дозволити лише `machine@<host>/<pid>`; 403 на чужий `key`; не перезаписувати fingerprint наявної частини | Так |
| **P0-02** | Повторний approved-стук замінює робочий `RemoteExecutor` (той самий наслідок, що P0-14, без рестарту) | Задача «висить» 35 хв, потім «руки не відповіли» | `registry.py:110-111` на кожен approved attach; `remote.py:31-50`; `brain.py:267-269`; `conversation.py:89`; 02 §3 (у звіті P1; тут P0, бо наслідок — втрата роботи, як у 01 P0-1, і фікс інший) | двері/ядро | Створювати executor лише на першому approve; на повторний стук оновлювати `about`/`skills`, не обʼєкт | Так |
| **P0-03** | Забуті слова лишаються фактами в промпті | Людина просить забути фразу — асистент цитує її щоходу | `book.py:134-156` (лише transcripts), `book.py:158-167`; `persist.py:505-541`; 04 P0-1, відтворено | памʼять | `Book.forget` знаходить похідні факти за source/текстом, викликає `facts.forget`, прибирає з `always()`; тест на промпт після forget | Так |
| **P0-04** | Платні проходи переучують забуте | «Я ж казав забути» — факт повертається наступного 5-хвилинного проходу | `consolidate.py:117-150`, `review.py:142-149,192-214`, `journal.py:143-173`; 04 P0-2, відтворено | памʼять | Фільтрувати `forget`-tombstones у `consolidate`, `notice`, `review_day`; спільний `Journal.visible_events()` | Так |
| **P0-05** | Забутий текст живе в копіях | Пошук/журнал/бекап/експорт ще містять фразу | `persist.py:352-357` (`turns.aggregated_text`), `journal.py:223-250`, бекапи; 04 P0-3 | памʼять | Forget чистить `turns.aggregated_text`, позначає журнал, виключає з `export`; задокументувати, що бекап до forget лишається | Так |
| **P0-06** | Мікрофон помирає назавжди | USB/Bluetooth/sleep — обличчя «слухає», але воно глухе до кінця сесії | `hearing.py` не імпортується; `device.py:181-200,302-358` без викликів; `channel.py:141-154`; 05 P0-1 | голос | Підключити `Hearing`/`silent_for` у `Voice.run`; `restart_input` з drain, `note_input_gap` і lock; один звіт на подію | Так |
| **P0-07** | Рестарт мозку посеред відповіді німіє назавжди | Мікрофон лишається вимкненим; наступна відповідь склеюється з попередньою | `voice.py:147-152` (чекає `_END` вічно), `channel.py:263-281`, `utterance.py:62-67,186-195`, `voice.py:200-208`; 05 P0-2 | голос | Таймаут/`cancel` для `_Reply`; `_Speaker.reset()` на reconnect; закрити utterance; ref-count mute | Так |
| **P0-08** | Агент бачить усі ключі й увесь диск | Prompt-injected агент читає `~/.heare/machine/api_token`, `~/.ssh`, env-ключі | `runner.py:242` (`dict(os.environ)`), `agents.py:199`; `acp.py:246-253` (немає `cwd=`); 06 H-01 | руки | Білий список env (лише обраний ключ + PATH/HOME); `cwd=` у `create_subprocess_exec`; обмежити `external_directory`; рішення про sandbox — у питаннях нижче | Так |
| **P0-09** | «Ніколи»-список не діє для Claude; `auto` дозволяє все | Агент робить `sudo`/`security`/`defaults` без питання | `agents.py:140-144` лише в opencode; `167-179,240-243`; `256-261` `auto` → allow; 06 H-02 | руки | Застосовувати `_NEVER` у `_decide` для всіх агентів і всіх режимів, включно `auto`; додати абсолютні шляхи й обгортки; тест «Claude в auto відмовляє» | Так |
| **P0-10** | «Так» не привʼязане до дії; `careful` переюзує його на всю задачу | Людина схвалила правку файлу — агент робить щось інше без питання | `agents.py:256-279`; `acp.py:394-436`; `conversation.py:1050-1054,1080-1081,967`; 06 H-03 | руки | Зберігати `what`+`detail` разом із дозволом; новий `change` — нове питання; не наслідувати `allowed` для `continues` без нового підтвердження | Так |
| **P0-11** | Два keeper-и / два годинники | Кожна частина двічі, дві відповіді, конкуруючі порти | `keeper.py:66-104` (немає lock/pidfile); `App.swift:48`; `install.sh:537,574`; `live.py` без `launchctl`; 07 F-1/F-50 | життя | Lock/pidfile у keeper; `make live` прибирає launchd-агенти; `make keeper` відмовляє, якщо живий інший | Так |
| **P0-12** | SIGKILL keeper-а осирочує частини | Після падіння keeper-а частини живі, новий keeper піднімає другі копії | `keeper.py:72-86,107-114` (один PID), `lifeline.py:25-37`; `Child.swift:75-88`; 07 F-2 | життя | `start_new_session` + kill process group; PDEATHSIG/батьківський watcher у частинах; новий keeper відмовляє, поки живі старі PID | Так |
| **P0-13** | `body → all` лишає `machine_token` | Усі локальні частини вічно 401 у 3-секундній петлі; логи ростуть | `config.py:134-140` (prefer machine_token), `setup.py:169-171`, `install.sh:434-438`; `part.py:204-219`; `keeper.py:85-86`; `machine.py:112-113` | життя | Видаляти `machine_token` при переході в `all`/`brain`; частина чекає токен, а не виходить; обмежити backoff | Так |
| **P0-14** | Рестарт мозку втрачає роботу, дозволи й питання | Сказав «стоп» — агент далі працює; результат зникає; список задач порожній | `state.py:38`, `conversation.py:240,968-970`; `brain.py:264` (лише повідомлення); `remote.py:36,48`; `server.py:456-458,488-493`; `hand.py:78-79` (ковтає 404); 01 P0-1; той самий ефект при живому re-knock — 02 §3 | ядро/двері | Гідратувати задачі; `step` на 404 → `stopped`; `result` на невідомий id → явна відмова й лог; зберігати `allowed`/питання або скасовувати їх на старті | Так |

---

## Консолідований список P1

> Таблиця згрупована за областями; один рядок = одна проблема, дублі злиті.
> Порядок: ядро/розум (01–03, P1-01…14), двері (02, P1-15…19), памʼять (04,
> P1-20…28), голос (05, P1-29…35), руки/нативні (06, P1-36…41), життя/тіла
> (07, P1-42…59). P1 суто внутрішні — компактним списком після таблиці.

| ID | Назва | Що відчуває людина | Доказ | Фікс (ескіз) | Тест |
|---|---|---|---|---|---|
| **P1-01** | `continues` успадковує попереднє «так» | Модель сама позначає нову роботу «продовженням» і виконує зміни без питання | `conversation.py:963-967`; `tools/delegate.py:20`; `tools/schemas.py:68-75`; 01 P1-1 | `allowed` не наслідувати для `change`; `continues` переносить контекст, не дозвіл | `test_a_yes_in_careful_is_not_reused_for_a_different_command` |
| **P1-02** | Тихий втрачений хід | Людина говорить — нічого не чує, немає `failed`, немає запису | `conversation.py:550` до `try` на 577, `532` (лише `finally`), `642`, `851`, `436/469/487/508` без await; 01 P1-2 | Обгорнути `system()`/`_say`/`_keep_the_promise`; ловити в `_turn` і говорити `FAILURE_LINE` | `test_a_broken_take_does_not_stop_the_conversation` |
| **P1-03** | Провалена робота рук показана як `done` | «Готово», хоча агент упав/401/зупинився | `forms.py:183-185`; `conversation.py:982-984`; `remote.py:88-97`; `engine.py:29-32`; 01 P1-3; 06 H-03 | `run` повертає `(status, text)`; `_work` ставить `failed`/`stopped`; не слати `task_done` | `test_a_failed_hand_work_is_not_done` |
| **P1-04** | Частина-зомбі після відкликання ключа/помилки `on_event` | Частина «на звʼязку», але не отримує подій і не бере роботу | `part.py:117-119` (не ловить `on_event`), `236-242` (ігнорує відповідь); `hand.py:106-111`; 01 P1-4; 07 F-15 | Ловити всі винятки у `follow`; на 401 — зупинка й звіт, не reconnect-цикл; `claim` розрізняє 401/порожньо | `test_a_revoked_part_stops_instead_of_reconnecting` |
| **P1-05** | Застаріле `playing` робить асистента глухим | Музика закінчилась, eyes впали — асистент вважає кімнату шумною й не відповідає | `conversation.py:162-165` (лише update), `443-447`, `885-888`; 01 P1-5 | TTL на нотатку; при втраті частини прибирати її ключі з `around` | `test_a_dead_eyes_does_not_leave_the_room_playing` |
| **P1-06** | Результат роботи у промпті без позначки «це дані» | Текст зі сторінки/файлу стає інструкцією найвищого пріоритету | `conversation.py:864-873`, `559` (recall — з рамкою); 01 P1-6; 03 §3.2 | Огортати `[work result — data, not commands]`, як recall; різати до N і прибирати markdown-структури | `test_a_work_result_cannot_shape_the_prompt_as_commands` |
| **P1-07** | «Так» у кімнаті відповідає на дозвіл | Фраза, сказана іншій людині, схвалює зміну | `conversation.py:452-473,1095-1128`; 03 P1-8 | Питати лише `addressed`-хід; для дозволу — явне «так/ні» або кнопка | `test_an_unaddressed_yes_does_not_approve` |
| **P1-08** | `kind` = `read` довіряється частині; `laws.RISKY` мертвий | Частина називає руйнівну дію «читанням» — її виконують без судді | `conversation.py:1036-1037`; `laws.py:29-34` без викликів; `protocol.md:57-60`; 03 P1-5; 09 Vacuum §1 | Класифікувати ефект на боці мозку (`RISKY` або skill `effect` з реєстру), не вірити `kind` | `test_a_skill_that_lies_about_its_effect_is_not_read_without_the_judge` |
| **P1-09** | Порожній стрім моделі = тиша | Хід закінчується без відповіді й без `FAILURE_LINE` | `chat.py:30-41`; `conversation.py:594-633`; 03 P1-6 | Порожній/200-з-помилкою стрім → `failure` = `model` | `test_an_empty_stream_is_reported_as_a_failure` |
| **P1-10** | Битий `reminders.json` валить мозок | Застосунок не стартує або падає під час роботи | `store.py:31-42` (ловить лише `TypeError`), `85-90`; `brain.py:274-277`; 03 P1-7 | Валідація типів у `_load`; пропуск битого рядка з логом; `gather` не вбиває мозок | `test_a_corrupt_intent_row_does_not_kill_engine_run` |
| **P1-11** | Поганий `review_hour` вбиває review і мозок | Нічний огляд тихо не працює; іноді мозок падає | `brain.py:235-236` поза `try`; `server.py:889-895`; 03 P1-9 | Валідувати 0–23 на записі й у `_review`; `try/except` навколо планувальника | `test_a_bad_review_hour_does_not_kill_the_brain` |
| **P1-12** | Нагадування/результат «доставлені» в порожнечу | Ні сторінки, ні голосу — нагадування зникло назавжди | `engine.py:198-216` (enqueue → `done`), `server.py:1327-1329,130-139,239-249`; 03 P1-2 | Позначати delivered лише після підтвердження живого слухача (page/voice) | `test_a_reminder_is_not_consumed_when_no_body_can_hear_it` |
| **P1-13** | Нагадування затримується анти-повтором | Нагадування на 10:00 приходить о 10:20 | `engine.py:160-164` (`reminders` після `due=[]`), `40-42`; 03 P1-3 | Обчислювати reminders до очищення `due`; винятки для weight 3 | `test_a_due_reminder_is_not_delayed_by_the_gap_after_a_remark` |
| **P1-14** | Результат показаний судді як слова власника | Агентський текст розширює те, що «попросив власник» | `conversation.py:564` (`role` = `user`), `judge.py:159-161`; 03 P1-4 | Окремий `role`/тег `[work result]`; суддя бачить лише справжні ходи людини | `test_a_previous_task_result_is_not_shown_as_the_owners_words` |
| **P1-15** | Повторний стук розширює `hears`/`channel`/`forms`/`approves` | Частина починає чути всю розмову або давати «так» без нового питання | `server.py:542-572`; `registry.py:144-147`; `skills.py:121-125`; 02 §2.2 | Включити ці поля у fingerprint; зміна → нове питання | `test_an_approved_part_cannot_widen_hears_on_a_knock` |
| **P1-16** | Reject/expire не відкликає ключ | Відхилена/прострочена частина далі пише свою смугу й чує події | `server.py:573-611`; `registry.py:264-278`; 02 §2.3 | `_decide(reject/expired)` → `auth.revoke`; `part_may` перевіряє стан | `test_a_rejected_part_keeps_no_key` |
| **P1-17** | Вікно = людина без обмежень | Браузер на іншій машині може змінити ключі, поставити оновлення, відновити бекап | `server.py:345-352,393-403`; `update.py:114-180`; 02 §2.4 | Дозволити вікну лише pult-маршрути; settings/keys/update/backup — master only | `test_a_window_cannot_point_the_brain_at_another_update_feed` |
| **P1-18** | Canvas/`screens` — код у origin людини | Віджет читає історію/памʼять, шле beacon, «говорить» як людина; `screens` — повний скрипт із cookie | `page.html:1483,1458-1465,1750-1758`; `canvas.py:57-63`; `server.py:1187-1201`; `screens.py:94-104`; 02 §6 | Усі віджети у sandbox-iframe; прибрати `say`/`memory` з bridge; sanitise `<style>/<form>/<img>`; `screens` — окремий origin або видалити | `test_a_live_widget_cannot_read_memory_or_say` |
| **P1-19** | Сторінка не перестукується після рестарту | Після рестарту мозку сторінка є, але її немає серед частин | `page.html:2556` vs `2580` (другий `onopen` перемагає); `registry.py:232-235`; 02 §3 | Один `onopen`, що кличе `knock()`; тест на один `onopen` | `test_the_stream_reconnects_and_knocks` |
| **P1-20** | Журнал і SQLite не одна транзакція | Хід є в журналі, але не знаходиться пошуком (або навпаки) | `book.py:63-96`; `conversation.py:909-911`; 04 P1-1 | Порядок journal→db з маркером відновлення; reconcile на старті | `test_book_keep_journal_then_db_crash_does_not_hide_the_turn` |
| **P1-21** | Немає `busy_timeout` | `database is locked` під час бекапу/другого процесу → хід зникає | `persist.py:141`, `facts.py:170`, `storage.py:173`; 04 P1-2 | `PRAGMA busy_timeout` + повтор із backoff на кожному зʼєднанні | `test_book_keep_survives_a_locked_database` |
| **P1-22** | Журнал не durable між процесами | Після збою живлення рядок загублено; битий рядок тихо пропущено | `journal.py:75-82,167-172,277-282,315-324`; 04 P1-3 | `flock` на append; fsync файлу й каталогу; cursor не переступає битий рядок | `test_a_torn_line_is_not_silently_skipped_by_the_cursor` |
| **P1-23** | Короткі справжні фрази не згадуються | «Я купив каву» не знаходиться, бо схоже на питання | `search.py:103,289-309`; 04 P1-4, відтворено | Класифікувати питання за синтаксисом/часом, не за кількістю слів; знизити `MIN_CHARS` | `test_short_statement_is_recalled` |
| **P1-24** | Українська інфлексія ламається на словах <5 літер | `кава` не знаходить `каву` | `fts.py:291-292`; 04 P1-5, відтворено | Префіксний/стемовий пошук для коротких токенів; словник закінчень | `test_short_ukrainian_word_matches_its_cases` |
| **P1-25** | Конфліктні факти обидва «поточні» | «Живеш у Києві» і «в Берліні» — обидва в промпті | `review.py:153-189`; `facts.py:375-388` (без виклику); 04 P1-6 | Викликати `supersede` на `(subject, predicate)` з новим значенням | `test_conflicting_values_are_not_both_current` |
| **P1-26** | `confidence: 0.0` стає підтвердженим фактом | Сумнівний здогад потрапляє в кожен промпт | `review.py:179-183,315-318`; 04 P1-7 | Поріг (напр. 0.6) → `pending`/відкинути; лог дропу | `test_confidence_zero_is_not_confirmed` |
| **P1-27** | Блокуючий SQLite/файли в async-гарячому шляху | Голос відповідає повільніше; UI «зависає» | `book.py:158-167`, `review.py:142-149,163-183,192-214`; 04 P1-8 | `asyncio.to_thread` для `always`, `notice`, `review_day`; кеш фактів на хід | `test_always_does_not_block_the_loop` |
| **P1-28** | Зламаний FTS = «нічого не памʼятаю» | Пошук тихо порожній назавжди | `persist.py:173-212,238-240`; 04 P1-9 | Перевірка parity/rebuild; health-лічильник у `/api/state` | `test_search_without_an_index_or_with_a_bad_match_returns_empty_not_raises` |
| **P1-29** | Sync HTTP на аудіо-петлі | Повільний мозок коштує втрачених фраз, не лише затримки | `voice.py:83-91,276-278`; `vad.py:131-136`; `device.py:119`; 05 P1-3 | `httpx.AsyncClient`; обмежити чергу звітів; не блокувати `_listen` | `test_a_slow_report_does_not_drop_a_sentence` |
| **P1-30** | `_Over.long` домальовується після кінця | Кивок «ага» стає перебиванням при повільному STT | `channel.py:155-162,173-179,190-199`; 05 P1-4 | Заморозити `long`/`speech_ms` на END | `test_the_long_flag_freezes_at_the_end_of_the_over_utterance` |
| **P1-31** | Повільний STT розрізає фразу на два ходи | Дві відповіді на одне речення | `turn.py:244-258,260-273,267-269`; `stt.py:49` (15 с) vs 8 с; 05 P1-5 | Dead-STT вікно ≥ STT timeout; не закривати хід із фрагментом | `test_a_slow_transcript_does_not_split_a_turn` |
| **P1-32** | Виняток TTS обходить 4 спроби й fallback | Відповідь не звучить; мозок вважає, що сказав | `channel.py:298-311`; `tts.py:47-52`; `voice.py:161-162`; 05 P1-6 | Ловити виняток синтезу й одразу йти в system voice; репорт `failed` | `test_a_synthesiser_exception_falls_back_to_the_system_voice` |
| **P1-33** | Кривий `said_over` вбиває розпізнавання | Один 200 без `turn` — частина мертва | `voice.py:121-127`; `channel.py:190-199`; 05 P1-7 | Guard на JSON/`turn`; трактувати як «не хід» | `test_a_non_json_said_over_verdict_does_not_kill_recognition` |
| **P1-34** | Збій відкриття пристрою = crash-loop | Голос не піднімається взагалі, keeper рестартить вічно | `channel.py:380-390`; `device.py:184-186` обіцяє протилежне; 05 P1-8 | Ловити `sounddevice`/`_open`; стан «глухий, але живий» + повтор | `test_a_boot_without_a_microphone_stays_alive` |
| **P1-35** | Mute мікрофона без ref-count | Мікрофон вмикається під час мовлення → чує себе | `utterance.py:186-195,198-205`; 05 P1-9 | Лічильник живих `mute_mic`-уттеренсів | `test_the_mic_stays_muted_until_the_last_live_utterance_closes` |
| **P1-36** | Таймаути рук зупиняють сесію, не CLI; мертвий CLI переюзується | «Агент не відповідає», і всі наступні задачі йдуть у той самий труп | `life.py:49-57`; `agents.py:235-254,285-304`; 06 H-04 | Перевіряти `returncode`/liveness; `close()` перед новим стартом; watchdog на `initialize` | `test_a_killed_agent_is_detected_and_restarted` |
| **P1-37** | «Стоп» не зупиняє команду | Довга `bash`-команда добігає після «стоп» | `agents.py:285-331` (не читає `desk.stopped`); `remote.py:61-81`; 06 H-05 | Опитувати `stopped` між подіями; `session.cancel` + kill process group | `test_stop_cancels_a_running_command` |
| **P1-38** | Кривий ACP-кадр вішає хід | Задача «висить», поки не спрацює 5-хвилинний idle | `acp.py:172-206,366-392,394-436`; 06 H-06 | Валідація форм; `finally` фейлить pending futures; таймаут на `_call` | `test_a_malformed_acp_frame_does_not_hang_the_turn` |
| **P1-39** | Нативні руки: мʼякий бюджет, немає таймауту моделі | Задача нативних рук висить до 35 хв | `native_hands.py:118-123,157-159,356-361`; 06 H-07 | `httpx` timeout; фінальний хід у бюджеті; таймаут `skilled()` | `test_the_native_forced_final_turn_is_inside_the_budget` |
| **P1-40** | Дубль імені скіла бере `effect` першої частини | `change` виконується без судді | `native_hands.py:255-265,185`; 06 H-08 | Ключ каталогу `pid + skill`; ефект брати з викликаної частини | `test_duplicate_skill_names_use_the_effect_of_the_part_called` |
| **P1-41** | Trust фіксує лише `change`-скіли | Частина тихо міняє `read`/`harmless` на справжню роботу | `skills.py:121-125,141-150`; `native_hands.py:185-188`; 06 H-09 | Fingerprint усіх скілів; зміна будь-якого → нове питання | `test_a_changed_harmless_skill_is_asked_about_again` |
| **P1-42** | Зміна ролі/URL/порту не рестартить частини | «Переїхав на інший мозок», а частини стукають у старий | `setup.py:142-179`; `keeper.py:88-104`; `part.py:200-203`; `App.swift:58-62`; 07 F-4 | Keeper перезапускає частини на зміну `config.toml`; порт — не frozen | `test_a_role_change_restarts_the_parts` |
| **P1-43** | Невдалий `make live` лишає брехливий маркер; rollback іде на реліз до хорошого | «Відкотився» — але не туди | `live.py:232` (маркер лише на успіх), `139-155`, `262-267`; 07 F-5 | Писати `live` до/після з відкатом; rollback цілить в останній робочий | `test_a_failed_live_keeps_a_truthful_marker_and_rolls_back_to_the_installed_release` |
| **P1-44** | Вбудований updater без відкату після swap | Новий застосунок не стартував — старого вже немає | `Updater.swift:71-108`; `.old` видаляється на старті `App.swift:35`; 07 F-6 | Видаляти `.old` лише після health-check дітей | `UpdaterTests.swift` (macOS-only) |
| **P1-45** | Форма налаштувань: DNS-rebinding і будь-який локальний процес | Чужий сайт/процес міняє роль, URL, ключі | `setup.py:83-94,246`; 07 F-10 | `Host` ∈ {127.0.0.1, localhost} + перевірка `Origin`; токен на форму | `test_a_rebound_host_cannot_drive_the_form` |
| **P1-46** | Зайнятий порт setup тихо вбиває налаштування | Меню «Налаштування…» відкриває вічне «waking up» | `setup.py:240-248`; `keeper.py:134-140`; `App.swift:68-71`; `Windows.swift:46-67`; 07 F-11 | Наступний вільний порт або явний стан у меню | `test_a_taken_setup_port_says_which` |
| **P1-47** | Зламана команда маніфесту — частина мертва назавжди; 3-секундна петля без стелі | Картка «увімкнено», частини немає | `keeper.py:35-47,72-76,94-96`; `29-32,85-86`; 07 F-12/F-13 | `try` навколо spawn; backoff 3→60; cap і стан «unwell» | `test_a_broken_run_is_retried_with_backoff_and_says_why` |
| **P1-48** | Немає токена на першому запуску / не-connect помилки — 3-секундна петля | Частини нескінченно падають, лог росте | `part.py:204-209,77-84,216-219`; `App.swift:48-49`; 07 F-14/F-15 | Чекати токен; розрізняти 401/404/500 і показувати стан | `test_a_part_waits_for_the_token` |
| **P1-49** | Тиша при відмові start-at-login / мікрофона / «Remove heare» | Людина вірить, що ввімкнено/видалено, а це не так | `Windows.swift:190-199`; `installer.html:236-240,252`; `Windows.swift:178-180`; `Uninstall.swift:37-43`; `App.swift:255-265`; 07 F-43/F-44/F-45 | Показувати реальний стан і причину; mic-denied — картка; uninstall — помилка в UI | `test_login_refusal_is_visible` |
| **P1-50** | `install.sh`: без відкату, без сум, пів-інсталяція | «Оновився» — а supervisor не створено | `install.sh:355-380,390,521-526`; 07 F-51/F-53 | Один шлях інсталяції (див. питання 5); суми для URL-коліс | `test_install.py` (новий shell-тест) |
| **P1-51** | URL-колеса без перевірки сум | Supply-chain/обірване завантаження | `install.sh:328-339`; 07 F-52 | `SHA256SUMS` як в updater-і | — |
| **P1-52** | Секрети в argv і shell history | Ключі видно в `ps` під час інсталяції | `install.sh:25,259-260,452-465`; 07 F-54 | Лише env/файл; прибрати `--deepseek-key`/`--groq-key` | — |
| **P1-53** | «Який застосунок спереду» не працює ніколи | Картка каже «тихо»; README бреше | `sense.py:19-28`; `packages/heare-eyes/pyproject.toml:6` без PyObjC; 07 F-7 | Додати PyObjC у wheel і build; інакше — чесне «unknown» | `test_front_app_is_readable_in_the_shipped_app` (macOS) |
| **P1-54** | Нотатки eyes губляться при рестарті мозку | Зміна застосунку/музики/присутності зникає назавжди | `watch.py:53-56`; `eyes.py:72-91`; `part.py:174-182`; 07 F-8 | Retry у `Door.note` або `Wire.report`; не оновлювати `told` до 200 | `test_a_lost_note_is_sent_again_after_the_brain_returns` |
| **P1-55** | Вимкнена погода/змінене місто лишає старий Now до 90 хв | «Вимкнув погоду, а вона ще в промпті» | `sources.py:85-102,118-133,36-40`; `persona.py:56-67`; 07 F-9/F-31 | На вимкнення/зміну — нотатка з `until <= now` | `test_turning_weather_off_expires_the_now_note` |
| **P1-56** | Власний голос вважається «іншою програмою» на `install.sh` | Після мовлення асистент перестає відповідати без імені | `sound.py:25,50-64`; `install.sh:355-380`; `machine.py:91-94`; 07 F-24 | Матчити `heare-voice` і `-m heare.voice`, не лише `-m heare` | `test_our_own_voice_is_not_playing` |
| **P1-57** | Зламаний сенсор бреше замість «невідомо» | «Тихо», «присутній», «музика спинилась» — хоча сенсор впав | `sense.py:38-39,43-46`; `sound.py:98-99`; `eyes.py:76-78`; 07 F-26 | Трійка станів (true/false/unknown); лог і картка | `test_a_failed_sensor_says_unknown` |
| **P1-58** | Опитування — віяло підпроцесів кожні 1.5 с | CPU/батарея; кілька `ps` на секунду | `watch.py:13,38-57`; `sense.py:34-37`; `sound.py:58-75`; 07 F-27 | Один `ps` на цикл, backoff без змін, пауза при lock | `test_the_poll_uses_one_ps_per_round` |
| **P1-59** | Локація йде назовні у двох напрямках, один не задокументований | Місто → Open-Meteo → модель | `weather.py:86-105`; `sources.py:131-133`; `sources.md:55`; 07 F-32 | Задокументувати Open-Meteo; опція «без геокодера» | `test_the_weather_source_names_both_recipients` |

### P1 суто внутрішні (без прямого відчуття людини)

- `about` частини без межі й без рамки в описі `delegate`-тула — контекст/інʼєкція (03 §3.2; `schemas.py:180-184`).
- `Intent.weight` і `about`-мердж описані, але не читаються (03 §5.2; `intent.py:20-21`, `store.py:53-61`).
- `resolve_llm` перевіряє наявність ключа, не здоровʼя; мовчазний fallback (03 §6.1).
- `chat` ретраїть 401/403/429 і ігнорує `Retry-After` (03 §6.3).
- `delegate.assign` мертвий під `Router`; substring-матч імені (03 §6.4).
- `brief` не ріже вихід моделі після переказу (03 §6.4).
- `router` без executor-ів дає `IndexError`; handoff довільний і переписує всі задачі (03 §2).
- `_keep_the_promise` поза бюджетом і телеметрією; look-бюджет = 6 викликів, а `said` каже 5 (03 §3.4).
- `budget.fit` рахує лише `content` і 4 символи/токен (03 §3.4).
- `persona.now_block` не згортає переводи рядків джерела (03 §3.2).
- Дедуп делегата за перетином слів хибно спрацьовує на коротких заявках (03 §4.2).
- `remind`: DST/`fold`, `int(bool)`; `read_body` без валідації схеми (03 §4.2).
- `en`-консент не приймає «okay, do it»; прості дієприкметники = обіцянка (03 §9).
- `keys.get` може торкатися Keychain у петлі (03 §6.6).
- `F-25` приватність — точний список назв, без bundle-id (07 F-25).
- `F-33` текст погоди не перевірений на інʼєкцію (07 F-33).
- `F-46` Keychain після ad-hoc оновлення не тестований (07 F-46).

---

## P2 одним списком

**Ядро.**
- `SILENT`-префікс ковтає «Silent night.» (`conversation.py:749-753,772-774`).
- `hand.take(failed=str)` віддає сирий текст винятку (`hand.py:121`; `cli_hands.py:435`).
- Токен у URL і лог-рядку (`part.py:101,118`).
- `state.tasks` росте без межі (`state.py:38`; `conversation.py:968`).
- Некеровані вхідні черги (`conversation.py:231-232`).
- Один `_asking` lock морить голодом питання/дозволи (`conversation.py:1049,1101`).
- Перерваний дозвіл лишається в `_permission` (`486,489-498,1055-1057`).
- `Settings.__getattr__` повертає порожній рядок (`config.py:118-125`).
- Порожній token-файл → 401 замість «немає ключа» (`config.py:134-140`).
- `config.set` без lock (`config.py:149-175`).
- `logs.setup` додає `Once` щоразу (`logs.py:50-59`).
- Кеш каталогу i18n (`i18n.py:46-48`).
- `words.catalog` читає приватний `_catalog` (`words.py:44`).
- `lifeline` матчить PID, не процес (`lifeline.py:32-35`).
- `pairing.pair` без схеми/ліміту, блокуючий (`pairing.py:14-22`).
- `State.parts` старіє при видаленні (`server.py:605`).
- `Task.status` без `handoff` (`forms.py:214`).
- `Surface`/`Tools` не в `__all__` (`__init__.py:4-15`).
- Бюджет ядра вичерпано рівно (`test_dependencies.py:42`).
- Немає `remove_*`/prune (`conversation.py:253-263`).

**Двері.**
- Події губляться без сигналу клієнту (`state.py:50-59`; `server.py:682`).
- Необмежені `Slow._seen`/`minted_at`/`Tally`/`_fading` (`server.py:100-111,365,397-399,193-214`; `registry.py:249-250`).
- Невалідовані `replay`/`n`/`speech_ms` (`server.py:718,757,670`).
- Блокуючий token-IO на кожен запит (`auth.py:56-64`; `server.py:149-161,1206,1193`).
- Zip-bomb і неатомарний `apply` (`backup.py:207,228-239`).
- Update SSRF/без allowlist, `.part` лишається (`update.py:114-170,147-154`).
- `forms`/`ui`/`about` не валідовані й ідуть у промпт (`server.py:549-556,798-813`; `registry.py:286-292`).
- Немає ліміту `result` скіла (`server.py:807-813`).
- `screens` без ліміту (`server.py:1184`).
- Права на token-файли після запису (`server.py:159-160`; `auth.py:30-31`).
- `/api/memory` без межі запиту (`server.py:949-954`).
- Дрейф протоколу: `v` поза реєстрацією, `control`/`call` у фільтрі, 4 КБ vs 2 КБ (02 §9).

**Розум.**
- `providers.ProviderConfig.timeout` і `LLM_STALL_TIMEOUT_SECS` мертві (`providers.py:35,209-213`).
- OpenAI SSE з битими рядками = «порожня відповідь» (`llm.py:193-201`).
- Anthropic без `message_stop`, `max_tokens=1024` (`llm.py:352,395-428`).
- `delegate.assign` мертвий, substring (`delegate.py:55-76`).
- `brief` не ріже вихід (`78-87`).
- `route.names[0]`, 10-с очікування, довільний handoff (`route.py:65-77,101,105-108`).
- Шість викликів look проти пʼяти в телеметрії (`conversation.py:631-642,653-718`).
- `persona` не згортає переводи рядків (`persona.py:67`).
- `budget` рахує лише content/4 символи (`budget.py:14,42-45`).
- `schemas.delegate` без межі (`schemas.py:180-184`).
- Дедуп делегата за словами (`delegate.py:28-34`).
- `read_body` без схеми, `effect` від частини (`bodies.py:18,29-31`).
- `review`-кнопка no-op (`features.py:219-222`).
- `Brain.run` без supervision (`brain.py:274-277`).

**Памʼять.**
- `TranscriptStore` мертвий; `actions`/`displays`/`usage_events`/`user_profile` нічого не обслуговують (`storage.py:166-528`; 04 §6.6).
- Rebuild `actions` губить `idx_actions_ts` (`storage.py:242`).
- `persist` обходить перевірку новішої схеми (`persist.py:155-171`).
- `notice`/`review_day` ріжуть вхід, але рухають курсор (`review.py:142-149,217-233`).
- Review і consolidation платять двічі (`brain.py:166-179`).
- `_clean_copy` підставляє чужий довший хід (`search.py:186-220`).
- Без ліміту токенів запиту (`fts.py:268-293`).
- `facts.search` без bm25/стемінгу й без екранування `%`/`_` (`facts.py:135-140,334,345`).
- Часткова ідемпотентність джерел (`199-210`).
- `supersede` не `BEGIN IMMEDIATE` (`375-388`).
- N+1 `recent_exchanges`/`role_session_turns` без `transcripts(turn_id)` (`persist.py:650-677,786-817`).
- Ріст `role_sessions.turn_ids` (`702-727`).
- Немає fsync каталогу (`review.py:73-80`; `journal.py:182-191`; `profile.py:93-100`).
- Немає таймауту моделі в review (`review.py:258-269`).
- Час процесу, не зона (`when.py:20-24`).
- «Минулого вівторка» = цей тиждень (`when.py:331,469-473`).
- `_decode_turn_ids` приймає bool/float (`persist.py:834`).
- `Profile._read` без межі (`profile.py:109-114`).
- `migrate` припускає всі колонки (`migrate.py:24,51`).
- Consolidation пише український `text` у факти → промпт (`consolidate.py:35-100`).
- Немає `integrity_check`/parity/checksum журналу (`persist.py:190-208`; `facts.py:186-197`).
- Старі посилання на `src/` (`persist.py:3-10`; `storage.py:1`).

**Голос.**
- Черга повна — дроп непомітний (`device.py:416-421`).
- `restart_input` без gap/drain/lock (`device.py:302-358`).
- `AudioIO.start` не ідемпотентний, часткове відкриття тече (`device.py:255-300`).
- Фіксовані VAD-пороги без калібрування (`vad.py:41-54`).
- `is_junk` відкидає будь-яке слово ≥14 символів (`hallucinations.py:185-188`).
- Courtesy-фільтр ковтає `ок`/`okay`/`yeah` (`hallucinations.py:78-117`).
- False accept `докер`/`гави`, false reject `hear` (`wake_phrases.py:40-74,159-168`).
- Немає вибору пристрою (`channel.py:386`).
- Частоти продубльовано в 9 місцях (05 §3.5).
- AEC: фіксовані 30 мс, пере-ресемплінг на чанк, 16 кГц reference, неактивний AEC не показаний (`aec.py:39`; `far_end.py:24,48-59`; `aec.py:70-83`).
- Необмежені `_jobs`/`_Reply._queue`/`_output_buffer`/`_sentences`/`_fragments`.
- `say -v ?` парситься двома пробілами (`channel.py:334-339`).
- `loud_ms`/`levels` — два Python-проходи, `_scale_int16` у callback (`device.py:406-407,464-465`).
- `_keeping` вважає `0` істиною (`channel.py:116-118`).
- `sounding` не оновлює `wire.values` (`voice.py:114-119`).
- `Voice.run` gather без `return_exceptions` (`channel.py:141-143`).
- `_post` ловить лише `HTTPError` (`voice.py:121-127`).

**Руки.**
- `ask`-сокет без автентифікації/таймауту/ліміту (`cli_hands.py:308-324`).
- Немає process group/wait, stderr тече (`acp.py:245,270-278`).
- `_which`-питання всередині 10 с (`native_hands.py:144-149,268-278`).
- Нативний стоп змагається з fire-and-forget (`hand.py:67-79`; `native_hands.py:118-159`).
- `OPENCODE_CONFIG` перекривається env (`agents.py:199-200,243`).
- Нативні кажуть «Done.» без дії (`native_hands.py:127-128,157-159`).
- Resume може вказати на биту сесію (`agents.py:301`; `cli_hands.py:412-417`).
- `create_subprocess_exec` без `cwd=` (`acp.py:246-253`).
- `_call`/`start`/`session` без таймауту (`acp.py:255-267,282-314,354-361`).
- Немає fallback на `allow_always` (`acp.py:418-436`).
- Необмежені `_answer`-задачі/`_pending` (`acp.py:237`).
- `Session.text`/`events` і `Terminal._said` необмежені.
- `pixels.clean` — regex, `_rgba` довіряє довжинам (`pixels.py:28-46,86-122`).
- `sim` росте, `Board.call` кидає (`sim.py:83-88,119-132`).
- `bridge` без таймауту, один запит (`bridge.py:37-87`).
- UI налаштувань блокує ~35 с (`cli_hands.py:349,113-146`).
- `logs.say`/stderr агента персистяться (`cli_hands.py:294-297,394,410`).
- `_sweep` PID reuse, `bool(false)` (`cli_hands.py:252-264,362-363`).

**Життя/тіла.**
- Keeper зупиняє лише дитину, не групу (`keeper.py:107-114`, F-16).
- Remove/re-add гонка (`keeper.py:97-98`, F-17).
- Права `machine_token` після запису (`setup.py:164-167`; `install.sh:425-428`, F-18).
- Логи `0644` і `~/.heare` прохідний (`Child.swift:90-108`; `install.sh:475,500`, F-19).
- Битий `config.toml` робить body мозком (`Config.swift:41-44,74`, F-20).
- Без master-token — вічне «waking up» (`App.swift:72-78,130`, F-21).
- `heare-keeper setup` падає трейсбеком на зайнятому порту (`keeper.py:152-154`, F-22).
- `kept` без guard (`keeper.py:88-104,117-131`, F-23).
- Looking off не скидає `told` (`watch.py:42-44`, F-28).
- Немає шляху відмови TCC (`sense.py:19-46`, F-29).
- Немає debounce для музики (`watch.py:46-52`, F-30).
- Київ за замовчуванням, геокодер `language=uk` (`sources.py:40`; `weather.py:87`, F-34).
- `sources.json` неатомарний (`sources.py:59-64`, F-35).
- Невдалий post не повторюється (`sources.py:135-144`, F-36).
- `look()` блокується, поки мозок відсутній (`part.py:161-173`; `sources.py:135-144`, F-37).
- Без jitter/backoff (`sources.py:35`, F-38).
- Chat не під наглядом (`chat.py:91-101`, F-39).
- Chat 33.9 % (`chat.py:57-101`, F-40).
- Два чати з тим самим ключем (`chat.py:98-100`, F-41).
- Термінальні краї (`chat.py:84-87,73`, F-42).
- Статус-меню без «Check for updates…» (`App.swift:141-185,286-297`, F-47).
- «heare X is out — update…» відкриває pult (`App.swift:146-149`, F-48).
- Немає health для remote brain (`App.swift:126-129`, F-49).
- `install.sh` без smoke-перевірки (`install.sh:584-587`, F-55).
- `--parts` звалені (`install.sh:467-470`, F-56).
- Немає тестів rollback (`test_release.py:25-46`, F-57).
- `make live` чекає 90 с і лише «approved» (`live.py:184-238`, F-58).
- `rollback TO` → `IndexError` (`live.py:257-258`, F-59).
- `make live` видаляє перед встановленням (`live.py:143-146`, F-60).

**Тести.**
- Мертвий `laws.risky` тестується як «ворота» (09 Vacuum §1).
- Сенсорний тест проходить на порожньому `[]` (§2).
- `Captured` приймає будь-який keyword (§3).
- `install.sh`-тест перевіряє підрядки (§4).
- Page-тести не запускають сторінку (§5).
- Skill-тест зберігає нереалізовний скіл (§6).
- Judge-eval не проганяється (§7).
- Voice тестується навколо, не крізь (§8).
- Тест «українська за замовчуванням» форсується фікстурою (§9).
- Relative/dynamic imports тікають (§10).
- Page-правило мови забороняє лише кирилицю (§11).
- `pytest.raises` без `match` (§12).
- Sleeps/polls/глобальні мутації (§13).
- `open_room` лишає `mkdtemp` (§Isolation).
- Немає HOME-пісочниці; немає `xdist`/`randomly`.

---

## Лежні перемикачі й брехня в UI

| Контроль / фіча | Що обіцяє | Що насправді | file:line |
|---|---|---|---|
| Перемикач мови (pult ⚙) | «те, що чуєш і бачиш» — усе перемикається | Сторінка/голос/меню міняються; `Core.words` і `Core.judge` зафіксовані на старті | `page.html:587,2519-2521`; `brain.py:147`; `core/conversation.py:221-222`; 08 §3.1; 03 §7 |
| Мова судді кімнати | Суддя розмовляє мовою інтерфейсу | Англійський промпт судді лишається до рестарту | `brain.py:147`; `core/conversation.py:221`; 08 §3.1 |
| `context_tokens` (файл) | Ліміт контексту | Зафіксований у `partial(...)`, зміна файлу нічого не робить | `brain.py:148`; 08 §3.1 |
| Тиха година / денний бюджет | Керуються налаштуваннями | Немає UI; значення знімаються в `Brain.__init__` | `brain.py:161-165`; `engine.py:66-86`; 08 §3.1 |
| Voice Microphone/Sound/Volume | Зберігаються | Лише в памʼяті; після рестарту знову on/1.0 | `voice.py:39-42`; `channel.py:120-131`; 08 §3 |
| Eyes «Look» | Зберігається | Лише в памʼяті; рестарт вмикає назад | `eyes.py:22-25,44-47`; 08 §3 |
| CLI hands «Take work» | Зберігається | Лише в памʼяті; рестарт знову бере роботу | `cli_hands.py:338,362-363`; 08 §3 |
| Backup «with sound» | Зберігається | Не зберігається; знову позначено | `page.html:657,2230`; 08 §3 |
| «Review now» | Запускає огляд | No-op, якщо сьогоднішній уже позначено; каже «done» | `brain.py:194-212`; `features.py:219-222`; 03 §7; 08 §3.2 |
| «Remember now» | Запускає прохід | Немає нових рядків — немає видимого результату | `features.py:242-271`; 03 §7 |
| Fact «Forget» | Видаляє факт | Робить `reject`, рядок лишається в БД | `page.html:1944-1948`; `server.py:991-996`; 08 §3.2 |
| Hearing labels («ми міряємо, як добре чую») | Міряє точність | `label`/`truth` ніхто не читає | `page.html:2150`; `heard.py:41-91`; 08 §3.2 |
| Поле ключа в pult | «Збережено, діє з наступної відповіді» | Ключ не перевіряється; може не працювати | `server.py:928-935`; `page.html:2510-2517`; 08 §3.2 |
| «Check for updates…» у меню | Є в меню (README) | Лише в головному меню при відкритому вікні; статус-меню немає | `App.swift:141-185` vs `286-297`; README:127-128; 07 F-47 |
| Setup «Start at login» | Увімкнено | Намальовано «on» до реєстрації; відмова лише в лог | `installer.html:236-240,252`; `Windows.swift:190-199`; 08 §3.2; 07 F-43 |
| «Parts from other computers wait until you let them in» | Кожна чужа частина чекає | `machine@`-частини auto-approved, картки немає | `server.py:559-570`; 08 §3.2 |
| Окремі `screens` / `ScreenMaker` | Вкладки над canvas | API є, UI немає, `ScreenMaker` не створюється | `server.py:1177-1201`; `screens.py:63-105`; 08 §1.6 |
| Conversation summaries / idle closing / role sessions | Підсумки розмов | Ніхто не викликає; розмови не закриваються | `persist.py:543-648,679-817`; 04 P1-11 |
| Overheard retention / `room` search | Чуже мовлення зберігається тиждень | `log_overheard` без викликів; `room=True` недосяжний | `search.py:18-24`; `persist.py:446-503`; 04 P1-10 |
| Імʼя «Дока» на чистій інсталяції | Так звати (README) | Немає `identity.json`; показує «heare», wake = лише «гава» | `persona.py:28-33`; `wake_phrases.py:145-158`; README:64; 08 §3.2 |
| «Access from outside — soon» | Майбутня дія | Не-дія | `installer.html:227-258`; 08 §1.7 |

---

## Користувацький функціонал

Скорочено з 08 §2; повна таблиця лишається там.

| Можливість | Стан | Що працює / де бреше |
|---|---|---|
| Говорити й чути відповідь (голос) | працює | Потрібні Groq + ключ моделі; DSP добрий, але глухота/німота — P0 |
| Писати в pult | працює | `POST message` → той самий `Core` |
| Перебивати / «стоп» | частково | Без робочого AEC мікрофон вимкнений під час мовлення; кнопка Stop працює |
| Режим «лише на імʼя» | частково/бреше | На чистій інсталяції лише «гава», не «heare»/«Дока» |
| Згадувати («що я казав про…») | працює | Кю-таблиця фіксована на старті; сам інструмент працює |
| Делегувати CLI-рукам | частково | Потрібен opencode/Claude Code; без нього немає `delegate` |
| Нативні дії (панель/світло/пін) | частково | Немає тіла в комплекті; потрібна окрема частина або `heare-sim` |
| Дозволи й питання агента | працює | Але «так» у кімнаті може схвалити (P1-07), `careful` переюзує (P1-01) |
| Нагадування | працює | Абсолютний/відносний час; 7 днів grace; затримка анти-повтором (P1-13) |
| Моменти / проактивні репліки | частково | Результати/нагадування працюють; «моментів присутності» немає в UI |
| Вилучення фактів | працює | Кожні 5 хв + нічний review; потребує моделі; `confidence 0.0` (P1-26) |
| Журнал / пошук / forget | працює | Але forget не чистить факти/копії (P0-03…05); короткі фрази (P1-23) |
| Fact «Forget» | частково/бреше | Це reject, не видалення |
| Бекап / відновлення | працює | Експорту немає; відновлення потребує supervisor-а |
| Профіль | працює | Три блоки, ліміти, атомарний запис |
| Частини on/off | працює | Лише фіксовані пʼять; кастомні/віддалені не в меню |
| Парування нової частини/вікна | працює | Код 8 символів, 5 хв, одноразовий |
| Схвалення віддалених частин | працює з застереженням | `machine@` не чекає картки |
| Оновлення | частково | Лише app-started brain; статус-меню без перевірки; rollback немає |
| Перемикач мови | бреше/частково | Див. таблицю вище |
| Start at login | частково | Відмова лише в лог |
| Видалення | працює | Меню-шлях не тестований; помилка лише `NSLog` |
| Hearing review | бреше | Мітки не читаються |
| Canvas | працює | Лише видалення/редагування; створює модель |
| Окремі screens | відсутнє | API без UI |
| Погода / Now | частково | Англійське речення в українському UI; старе Now до 90 хв |

---

## План тестів

Джерела: план 09 (30 тестів) плюс «missing tests» з 01–07. Дублі злиті;
хвилі впорядковані за ціною помилки. Деталізація 09 збережена для хвиль 1–2.

### Хвиля 1 — P0: гроші, приватність, безпека

| # | Назва | Файл | Що asserts | Яку прогалину закриває |
|---|---|---|---|---|
| 1 | `test_a_machine_key_cannot_claim_another_parts_name` | `test_api.py` | `POST /api/bodies` з machine-токеном і `key: opencode@mac` → 403 або pid примусово власний | 02 §2.1; жодного тесту на чужий pid |
| 2 | `test_an_approved_part_cannot_widen_hears_on_a_knock` | `test_api.py` | повторний стук з `hears: all` → pending/нове питання, не auto-approve | 02 §2.2; `test_api.py:1909` лише `change`-скіли |
| 3 | `test_a_rejected_part_keeps_no_key` | `test_api.py` | approve → re-knock → reject → старий токен 401 | 02 §2.3; `test_api.py:650` стартує без токена |
| 4 | `test_a_pending_part_writes_nothing_and_hears_nothing` | `test_api.py` | поки pending: `POST /api/input` і `GET /api/events` → 403/порожньо | 02 §2.3 |
| 5 | `test_a_window_cannot_point_the_brain_at_another_update_feed` | `test_api.py` | вікно `POST /api/settings {update_feed: http://evil}` → 403 | 02 §2.4; RCE через свій feed |
| 6 | `test_the_settings_form_refuses_another_origin` | `test_setup.py` | `Origin: https://evil.example` → 403 «another page…»; без `Origin` — працює | 09 #8; `setup.py:83-94` |
| 7 | `test_a_rebound_host_cannot_drive_the_form` | `test_setup.py` | `Host: evil.test` + `Origin: http://evil.test` → 403 для `/part`, `/role body` | 07 #4; DNS-rebinding |
| 8 | `test_forget_removes_derived_facts_from_the_prompt` | `test_forget.py` | `Book.keep` → review → `forget` → `always()` не містить факту | 04 P0-1, відтворено |
| 9 | `test_forget_stops_the_paid_passes_relearning` | `test_forget.py` / `test_consolidate.py` | після forget `consolidate` → `added == 0`; `notice` не бачить рядка | 04 P0-2, відтворено |
| 10 | `test_forget_clears_aggregated_text_and_export` | `test_persist.py` / `test_journal.py` | `turns.aggregated_text` і `Journal.export` не містять тексту | 04 P0-3 |
| 11 | `test_agent_env_contains_only_the_chosen_key` | `test_cli_hands.py` | дочірній env не має `DEEPSEEK_API_KEY`/`GROQ_API_KEY`/`AWS_*`; має лише обраний | 06 #1; H-01 |
| 12 | `test_claude_in_auto_still_denies_the_never_list` | `test_reach.py` | `sudo`/`security`/`defaults` → reject у `auto` | 06 #2; H-02 |
| 13 | `test_an_old_actions_table_is_rebuilt_without_losing_rows` | `ported/test_storage.py` | старий `actions` з `decision_id NOT NULL` → рядок живий, колонка nullable, другий `init()` no-op | 09 #11; `storage.py:214-249` |
| 14 | `test_the_voice_body_closes_its_device_when_the_brain_goes` | `test_voice_body.py` | `device.stop` один раз, `speaker.cancel`, задача завершується | 09 #12; `voice.py:240-256` |
| 15 | `test_a_second_keeper_does_not_start_a_second_copy` | `test_keeper.py` | два `kept(folder)` → другий виходить ненульово; `pgrep` = 1 | 07 #1; F-1 |

### Хвиля 2 — P0: голос, руки, життя

| # | Назва | Файл | Що asserts | Яку прогалину закриває |
|---|---|---|---|---|
| 1 | `test_a_finished_utterance_reaches_the_brain_as_heard` | новий `test_voice_channel.py` | після `_listen`/`_recognise`/`_turns` рівно один `port.heard(text=…)`, `nothing` не кликали | 09 #1; `channel.py:141-255` (45.9 %) |
| 2 | `test_a_voice_over_the_reply_is_a_said_over_not_a_turn` | той самий | `said_over(speech_ms, long)`; `False` — без `heard`, `True` — хід | 09 #2; barge-in |
| 3 | `test_four_silent_syntheses_fall_back_to_the_system_voice` | `test_voice_english.py` | 4 виклики `synthesise`, потім PCM system voice; порожній `b""` → `[]` | 09 #3; `channel.py:283-311` |
| 4 | `test_an_early_exit_kills_ffmpeg_and_its_feeder` | новий `test_tts.py` | `proc.kill`, `proc.wait`, feeder done/cancelled, зайвих задач немає | 09 #4; `tts.py:121-153` |
| 5 | `test_the_hands_take_a_job_and_post_its_result` | `test_cli_hands.py` | claim → step/permit → `POST /api/executor/7/result {status: done}` | 09 #5; `cli_hands.py:376-437` |
| 6 | `test_a_hands_restart_waits_for_the_job_in_flight` | `test_cli_hands.py` | `RESTART` лише після result POST; session-файл записано | 09 #6; `cli_hands.py:418-457` |
| 7 | `test_the_keeper_kills_a_part_that_ignores_sigterm` | `test_keeper.py` | `terminate` → `kill` → другий `wait`; без зависання тесту | 09 #7; `keeper.py:107-114` |
| 8 | `test_a_sigkilled_keeper_leaves_no_orphans` | `test_keeper.py` | SIGKILL supervisor → новий keeper не бачить старих PID | 07 #2; F-2 |
| 9 | `test_switching_from_body_to_all_forgets_the_machine_token` | `test_config.py` | `machine_token` зник; `Settings.secret` = master; knock прийнято | 07 #3; F-3 |
| 10 | `test_the_voice_loop_reports_a_dead_mic_and_reopens_it` | `test_voice_channel.py` | `silent_for > 20` → один звіт + один `restart_input`; без шторму | 05 #13; P0-06 |
| 11 | `test_a_brain_restart_mid_reply_starts_a_new_utterance` | `test_voice_body.py` | reply A закрито/скасовано, reply B — новий utterance, mute не залип | 05 #16; P0-07 |
| 12 | `test_the_mic_stays_muted_until_the_last_live_utterance_closes` | `ported/test_mouth.py` | закриття новішого не вмикає мікрофон, поки старший живий | 05 #17; P1-35 |
| 13 | `test_a_failed_live_keeps_a_truthful_marker_and_rolls_back_to_the_installed_release` | `test_live.py` | live=A, B не стартував → маркер = A або rollback цілить A | 07 #8; F-5 |
| 14 | `test_a_language_switch_changes_words_and_judge_without_a_restart` | `test_brain_run.py` | після зміни `language` наступний хід розуміє «стоп»/«так» і суддя українською | 03 §10; P0-мова |
| 15 | `test_a_second_knock_keeps_the_executor_and_its_job` | `test_api.py` | claim → re-knock → `result` того ж id прийнято, задача резолвиться | 02 §3; P0-02 |

### Хвиля 3 — P1: назви й один рядок

- `test_the_stream_reconnects_and_knocks` — рівно один `onopen`, кличе `knock` (P1-19).
- `test_a_malformed_acp_frame_does_not_hang_the_turn` — битий кадр фейлить pending, не вішає (P1-38).
- `test_a_killed_agent_is_detected_and_restarted` — мертвий CLI не переюзується (P1-36).
- `test_stop_cancels_a_running_command` — «стоп» вбиває команду, не лише сесію (P1-37).
- `test_a_yes_in_careful_is_not_reused_for_a_different_command` — нове `change` = нове питання (P1-01).
- `test_an_unaddressed_yes_does_not_approve` — «так» не в адресу не схвалює (P1-07).
- `test_a_failed_hand_work_is_not_done` — `failed` не стає `done` (P1-03).
- `test_an_empty_stream_is_reported_as_a_failure` — порожній стрім говорить failure (P1-09).
- `test_a_corrupt_intent_row_does_not_kill_engine_run` — битий рядок пропущено (P1-10).
- `test_a_bad_review_hour_does_not_kill_the_brain` — 25 годин не валить (P1-11).
- `test_a_reminder_is_not_consumed_when_no_body_can_hear_it` — без слухача не `delivered` (P1-12).
- `test_a_due_reminder_is_not_delayed_by_the_gap_after_a_remark` — weight 3 каже вчасно (P1-13).
- `test_a_previous_task_result_is_not_shown_as_the_owners_words` — суддя не бачить результату як власника (P1-14).
- `test_book_keep_survives_a_locked_database` — `busy_timeout`/retry (P1-21).
- `test_a_torn_line_is_not_silently_skipped_by_the_cursor` — битий рядок видно (P1-22).
- `test_short_statement_is_recalled` — коротка фраза знаходиться (P1-23).
- `test_short_ukrainian_word_matches_its_cases` — `кава`/`каву` (P1-24).
- `test_conflicting_values_are_not_both_current` — supersede працює (P1-25).
- `test_confidence_zero_is_not_confirmed` — 0.0 не стає фактом (P1-26).
- `test_a_slow_report_does_not_drop_a_sentence` — sync HTTP не краде фразу (P1-29).
- `test_the_long_flag_freezes_at_the_end_of_the_over_utterance` — кивок не стає перебиванням (P1-30).
- `test_a_slow_transcript_does_not_split_a_turn` — одна фраза = один хід (P1-31).
- `test_a_synthesiser_exception_falls_back_to_the_system_voice` — виняток не німить (P1-32).
- `test_a_non_json_said_over_verdict_does_not_kill_recognition` — кривий 200 не вбиває (P1-33).
- `test_a_boot_without_a_microphone_stays_alive` — crash-loop замінено на «глухий» стан (P1-34).
- `test_the_native_forced_final_turn_is_inside_the_budget` — фінальний хід у 15 с (P1-39).
- `test_duplicate_skill_names_use_the_effect_of_the_part_called` — ефект викликаної частини (P1-40).
- `test_a_changed_harmless_skill_is_asked_about_again` — fingerprint усіх скілів (P1-41).
- `test_a_role_change_restarts_the_parts` — зміна ролі перезапускає (P1-42).
- `test_a_failed_live_keeps_a_truthful_marker_and_rolls_back_to_the_installed_release` — вже в хвилі 2.
- `test_a_rebound_host_cannot_drive_the_form` — вже в хвилі 1.
- `test_a_taken_setup_port_says_which` — або вільний порт, або явний стан (P1-46).
- `test_a_broken_run_is_retried_with_backoff_and_says_why` — backoff і причина (P1-47).
- `test_a_part_waits_for_the_token` — без токена чекає, не падає (P1-48).
- `test_login_refusal_is_visible` — відмова start-at-login у UI (P1-49).
- `test_front_app_is_readable_in_the_shipped_app` — macOS-only, PyObjC у wheel (P1-53).
- `test_a_lost_note_is_sent_again_after_the_brain_returns` — нотатка eyes retry (P1-54).
- `test_turning_weather_off_expires_the_now_note` — Now зникає одразу (P1-55).
- `test_our_own_voice_is_not_playing` — `heare-voice` не «інша програма» (P1-56).
- `test_a_failed_sensor_says_unknown` — трійка станів (P1-57).
- `test_the_poll_uses_one_ps_per_round` — одне віяло на цикл (P1-58).
- `test_a_work_result_cannot_shape_the_prompt_as_commands` — рамка даних (P1-06).
- `test_a_skill_that_lies_about_its_effect_is_not_read_without_the_judge` — `effect` не від частини (P1-08).

### Хвиля 4 — P2: назви й один рядок

- `test_a_drawing_that_cannot_be_saved_is_still_drawn` — canvas не падає на OSError.
- `test_a_block_name_that_is_not_ours_is_refused` — `ValueError` на `put`.
- `test_the_model_arranges_widgets_and_refuses_a_bad_shape` — типи не ламають хід.
- `test_the_tally_counts_a_priced_and_an_unpriced_call` — `cost_usd`/`unpriced`.
- `test_a_malformed_body_is_a_400_not_a_500` — `/api/message`.
- `test_memory_needs_a_query` — `/api/memory` без `q` → 400.
- `test_a_backup_skips_a_missing_section` / `test_a_zip_holding_extra_files_is_refused` — бекап.
- `test_a_reopened_acp_session_asks_the_agent_to_resume` — `session/resume`.
- `test_an_agent_request_that_is_not_permission_is_refused` — `-32601`.
- `test_a_cancelled_permission_answers_the_agent_with_cancelled` — `outcome: cancelled`.
- `test_a_canvas_bad_tool_call_does_not_break_the_turn` — 09 #28.
- `test_a_virtual_board_refuses_an_unknown_skill_and_bad_arguments` — 09 #29.
- `test_a_failed_weather_note_is_logged_and_the_loop_continues` — 09 #30.
- `test_the_chat_printer_mutes_and_unmutes_itself` — 09 #25.
- `test_the_chat_line_survives_broken_utf8` — 09 #27.
- `test_dead_sockets_are_swept_and_live_ones_kept` — 09 #21.
- `test_a_canvas_bad_tool_call…` — вже вище.
- `test_every_provider_timeout_reaches_the_client` — мертвий `ProviderConfig.timeout`.
- `test_a_200_response_with_an_error_body_is_a_failure_not_an_empty_answer` — 03 §6.1.
- `test_a_task_fails_with_a_sentence_not_an_index_error` — `route.names[0]`.
- `test_an_answer_with_a_bare_participle_is_not_a_promise` — `promises_en.py`.
- `test_english_consent_accepts_okay_do_it` — `en.py:236-251`.

---

## Порядок робіт

Порядок: спершу те, що краде гроші/приватність/безпеку, потім тихі збої, потім
голос/руки, а UI-правда — останньою, бо вона дешева й не блокує решту.

| Крок | Розмір | Що входить | Що розблоковує |
|---|---|---|---|
| **1. Зафіксувати двері** | M | P0-01/02; P1-15/16/17; форма: P1-45; тести хвилі 1 №1–7, 15. `machine@` pid pin, allowlist вікна, fingerprint `hears`/`approves`, revoke на reject, Host/Origin | Довіра до решти; знімає RCE й крадіжку ідентичності |
| **2. Забуття, яке забуває** | M | P0-03/04/05; P1-25/26 (сусідні); тести №8–10. Forget фактів, tombstone-фільтр, `aggregated_text`/export | Приватність; без нього не можна обіцяти «памʼять належить тобі» |
| **3. Мозок не падає й не губить хід** | L | P0-14; P1-10/11/20/21/22; тести №13, хвиля 2 №15, хвиля 3 (intent/busy/torn) | Тиждень без рестарту; знімає «мозок вийшов» |
| **4. Голос: глухота й німота** | M | P0-06/07; P1-29…35; тести хвилі 2 №1–4, 10–12 | Голос як канал, а не демо |
| **5. Руки: межа й стоп** | M | P0-08/09/10; P1-36/37/38/39/40/41; тести хвилі 1 №11–12, хвилі 2 №5–6 | Безпечна робота агента; «стоп» справді стоп |
| **6. Життя: один keeper, один токен** | M | P0-11/12/13; P1-42/43/46/47/48; тести хвилі 1 №14–15, хвилі 2 №7–9, 13 | «Без термінала» — центральна обіцянка Г7 |
| **7. UI-правда** | S/M | Мова (P0-мова, тест хвилі 2 №14), мертві перемикачі, `identity.json`, hearing labels, review-now; P1-18/19/44/49 | Людина перестає вірити тому, чого немає |
| **8. Якість памʼяті й P2-хвиля** | L | P1-23/24/27/28; P2-список; хвиля 4 | Recall, який справді згадує; менше тертя |

---

## Відкриті питання до людини

1. **Чи мають machine-ключі існувати взагалі?** Якщо так — чи привʼязуємо pid до
   машини назавжди (P0-01), чи лише дозволяємо `machine@<host>`?
2. **Вікно = людина?** Чи може браузер на іншій машині міняти ключі/оновлення/бекапи,
   чи це лише «тіло» з pult-правами (P1-17)?
3. **Пісочниця для агента?** Seatbelt/контейнер зараз, чи приймаємо кооперативну
   модель і лише чистимо env (`P0-08`)? Від цього залежить обсяг робіт.
4. **Мертві таблиці — лишити чи видалити?** `actions`, `displays`, `usage_events`,
   `user_profile`, `role_sessions` (04 §6.6). Видалення — це міграція; лишення —
   вічна плутанина.
5. **Два шляхи інсталяції.** `install.sh`/launchd і `make live`/app — який один
   лишаємо (P0-11)? Другий треба або прибрати, або зробити явно «серверним».
6. **Імʼя на чистій інсталяції.** Писати `identity.json` з «Дока» і wakephrase
   «дока»/«heare» за замовчуванням, чи лишити «гава» (08 §3.2)?
7. **HTTPS зараз?** README каже «з телефона ще ні». Чи інвестуємо в TLS/тунель
   зараз, чи лишаємо локально й лише документуємо межу?
8. **Аудит — у репо?** Чи переносимо `00-synthesis.md` у `docs/product/` як
   живий документ, і чи лишаємо `screens` (API без UI) або видаляємо?

---

## Джерела

| Файл | Що це | Рядків |
|---|---|---:|
| `01-core.md` | ядро: контракт `forms.py`, ізоляція, тихий хід, втрата роботи при рестарті | 589 |
| `02-brain-api.md` | двері: 67 маршрутів, авторизація, машина/вікно, canvas, бекап, оновлення | 752 |
| `03-brain-mind.md` | розум: turn flow, routing, prompt, tools, engine, model, features, mind | 812 |
| `04-memory.md` | памʼять: три власники БД, інтеграція, пошук, факти, забуття | 728 |
| `05-voice.md` | голос: VAD/STT/TTS/AEC, глухий мікрофон, рестарт посеред відповіді | 810 |
| `06-hands.md` | руки: ACP, env/FS, never-floor, стоп, нативні руки, sim | 811 |
| `07-bodies-lifecycle.md` | тіла й життя: keeper, eyes, sources, Mac shell, install/update | 717 |
| `08-user-surface.md` | те, що бачить людина: меню, pult, налаштування, можливості, брехня | 608 |
| `09-tests.md` | сам набір тестів: фальшива впевненість і 30 тестів | 1001 |

---

## Що я рекомендую зробити першим

**Один перший крок: закрити двері — P0-01 (machine-ключ не стає чужою частиною),
P1-17 (вікно не має прав на settings/update/backup) і P1-45 (форма приймає лише
`Host` 127.0.0.1/localhost із перевіркою `Origin`)** — разом із тестами 1–7 хвилі 1.
Це найдешевша робота з найбільшим виграшем: вона знімає RCE через `update_feed`,
крадіжку ідентичності виконавця й читання всієї розмови, не торкаючись ані
аудіо-DSP, ані памʼяті. Після неї кожен наступний крок — забуття, мозок, голос,
руки, keeper — робиться вже в системі, де «частина» означає частину, а не будь-кого.

---

## Стан виконання

| Крок | Стан | Що зроблено | Що лишилось відомим |
|---|---|---|---|
| 1. Двері | **зроблено 29.10** | machine-ключ привʼязаний до pid; повторний стук не замінює `RemoteExecutor`; зміна `channel`/`forms`/`hears`/`approves` — нове питання; reject/expire відкликає ключ; вікно з іншої машини — лише allowlist пульта (усе інше 403 за замовчуванням, з тестом-переписом маршрутів); форма — лише loopback `Host` + `Origin`. 11 нових тестів | залишок: машина з іменем, що збігається з хостом локального маніфесту, може заявити pid цього маніфесту до першого стуку; legacy-запис довіри оновлюється без повторного питання (один раз) |
| 2. Забуття | **зроблено 29.10** | `Book.forget` прибирає похідні факти з промпту; один спільний `Journal.visible_events` для consolidate/notice/review_day; забуте вичищається з `turns.aggregated_text`, `export` і **з самих файлів журналу** (`compact` під `flock`, temp+`os.replace`; baseline рухається лише після успішного reconcile і стає на початок останнього файлу — подія, що прийшла під час forget, не губиться); конфліктні факти заміщуються; `MIN_CONFIDENCE = 0.6`. 12 нових тестів | старі бекапи, `heard/` записи й `user.md` не переписуються (снапшот/поза пакетом); SQLite free pages не вакуумуються |
| 3. Мозок не губить хід | **зроблено 29.10** | рука дізнається, що мозок її забув (404 на `step` = стоп, `Gone` на ask/permit, відмова результату видно в лозі); хід завжди закінчується словом і подією `failed`; `Outcome` — failed/stopped не стає `done`; битий `reminders.json` і поганий `review_hour` не валять мозок; журнал→SQLite відновлюється після падіння (`journal_applied`), `busy_timeout` + retry, `flock`+fsync, битий рядок не ковтається | задачі не переживають рестарт як дані — контракт «мозок не знає → рука зупиняється на наступному кроці»; baseline журналу не просувається (повний скан при старті, не повільність) |
| 4. Голос | **зроблено 29.10** | вотчдог глухоти (`hearing` більше не мертвий, один звіт + один `restart_input` з backoff); рестарт мозку посеред відповіді закриває reply і не лишає мікрофон вимкненим; ref-count mute; звіти з черги, не з аудіо-петлі; `_Over.long` заморожений на END; dead-STT вікно ≥ таймаут розпізнавання; виняток синтезу → системний голос; битий `said_over` не вбиває розпізнавання; пристрій без мікрофона живе; TTS-процес добивається | усе, що потребує заліза (реальний unplug/sleep, AEC у кімнаті, Edge/ffmpeg), лишається за `spine_live` |
| 5. Руки | **зроблено 29.10** | агент стартує з білого списку env (лише обраний ключ + PATH/HOME/cwd), `cwd=~/heare-work`, своя сесія процесів; «ніколи» діє для всіх агентів і режимів, включно `auto`; «так» привʼязане до `(what, detail)`, `continues` не успадковує дозвіл; мертвий CLI не переюзується; «стоп» шле `session/cancel` і вбиває групу; битий ACP не вішає хід; бюджет і таймаути нативних рук; дубль скіла бере ефект викликаної частини; fingerprint усіх скілів | **немає OS-пісочниці**: агент усе ще читає будь-який файл, доступний людині; межа — env/cwd/дозволи, не контейнер; після оновлення кожна частина один раз спитає згоду (змінений fingerprint) |
| 6. Життя | **зроблено 29.10** | один keeper (`flock` + pid), друга копія виходить із кодом 1; частини в окремій сесії, стоп вбиває групу, сироти самі виходять через `lifeline`; перехід у `all` видаляє `machine_token`/`url`; частина без токена чекає з backoff; зміна ролі/URL рестартить частини; битий маніфест — backoff і видима причина; fingerprint наглядача включає `api_token` і маніфести, тож заміна мозку чи правка команди будить припарковану частину; `make live` пише `healthy` лише після health-wait, відкат — на останній здоровий; `.old` живе до health-check; відмови login/mic/uninstall видно; `install.sh` на Darwin відмовляє й посилає до застосунку, launchd прибрано; суми для коліс, ключі не в argv | реальні `make live`/`make soak`/оновлення застосунку на цьому Mac ще не прогнано; Swift-тестів у `apps/mac` немає — перевірено `swift build` і читанням; відхилену частину не можна повторно схвалити з пульта — потрібна зміна конфігу/маніфесту або перезапуск наглядача |
| 7. UI-правда | **зроблено 29.10** | мова діє без рестарту (`Core.words`/`judge` читаються на хід); `identity.json` з «Дока»; перемикачі мікрофона/звуку/гучності, «Look», «Take work» переживають рестарт; «Review now» каже, коли сьогодні вже оглянуто; ключ з пульта перевіряється; сторінка перестукується після рестарту (один `onopen`); віджети канвасу — лише `sandbox="allow-scripts"` без `say`/памʼяті; збережений HTML санітизується; `screens` з CSP `default-src 'none'`; тексти про слух і назву чесні | немає браузерного runtime-тесту: властивості сторінки перевірені на рівні джерела, а не в браузері |
| 8. Памʼять і P2 | **зроблено 29.10** | короткі фрази згадуються (питання — за синтаксисом, не за довжиною); `кава`/`каву` (префіксний стем за кирилицею); `always` не блокує цикл; зламаний FTS перебудовується і має health; LIKE-екранування, `BEGIN IMMEDIATE`, індекс `transcripts(turn_id)`; «яка програма спереду» працює (PyObjC у `heare-eyes`, перевірено: `Google Chrome`); нотатки eyes переживають рестарт; погода гасне при вимкненні/зміні міста; свій голос не вважається «грає»; зламаний сенсор каже `unknown`; один `ps` на цикл; локація задокументована | прибирання старих фактів/журналів (retention) свідомо не робилось — це рішення людини, не тихе видалення; SQLite free pages не вакуумуються; WAL не чіпали ; після незалежного ревʼю: нелокальне вікно — тепер allowlist пульта (skills/tasks/executor/approve закриті), baseline після forget ставиться на кінець журналу (без дублів старої історії), відхилена/прострочена частина паркується, а не питає щохвилини |
