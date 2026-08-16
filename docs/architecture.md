# Архітектура heare — станом на 16.08.2026

Один процес. Чотири шари. Голосовий тракт — власний, без фреймворку.

```
┌─ ОБОЛОНКА ─────────────────────────────────────────────────────────┐
│  менюбар (rumps) · React-дашборд ──HTTP+токен──▶ api.py :9780      │
└──────────────┬──────────────────────────────────────┬──────────────┘
               │ читає State / voice_state / БД       │ POST /inject
┌─ МІСТ  src/daemon/spine_engine.py ──────────────────▼──────────────┐
│  поллер контролів (200мс) · поллер ролей (500мс) · полер inject     │
│  пише: running · voice_state · agent_state · role_* · roles_*       │
│  читає: mute_mic · mute_bot · interrupt_off · cancel · gain/volume  │
└──────────────┬──────────────────────────────────────────────────────┘
┌─ ДВИГУН  src/spine/  (активний: engine = "spine") ─────────────────┐
│ мікрофон → audio_io → aec → vad → [stt-воркер → Groq] → turn       │
│    ▲          │ gain/mute        │ ґейт loud_ms + фільтр галюц.    │
│    │          └ START при playing → barge-in (динамік+far-end+     │
│    │                                 стрім+тули)                    │
│ динамік ◀ tts ◀ sentences ◀ DeepSeek(SSE) ◀ wake ◀ ОДИН ГОДИННИК   │
│           voicing            +tool_calls    ролі: voice/log/hints   │
└──────────────┬──────────────────────────────────────────────────────┘
┌─ РУКИ  src/agent/ ─────────────────────────────────────────────────┐
│  delegate → Hands: цикл ≤12 кроків, ~50 тулів, джоби в БД          │
│  результат → loop.inject() як репліка користувача (повз wake)      │
└──────────────┬──────────────────────────────────────────────────────┘
┌─ СУБСТРАТ ─────────────────────────────────────────────────────────┐
│  heare.db: transcripts · turns · role_sessions · memories(FTS)     │
│            usage_events · jobs · actions                            │
│  ~/.heare: config.toml · .env · api_token · roles/ · logs/          │
└─────────────────────────────────────────────────────────────────────┘
```

## Хто чим володіє

| Дані | Хто пише | Хто читає |
|---|---|---|
| `transcripts`, `turns` | `spine/persist.py` | контекст промпта, дашборд, пошук |
| `role_sessions` | `role_session.py` через `persist` | відновлення після рестарту |
| `memories` (+FTS) | тули `remember` / `forget` | блок пам'яті в промпті, `recall` |
| `usage_events` | `spine/usage.py` | картка витрат |
| `jobs`, `actions` | `agent/hands.py` | «що я робив» |
| `turns.jsonl` | `spine/telemetry.py` | вимірювання якості розмови |

Колонка `transcripts.mode` поліморфна: старий двигун писав `'assistant'`
для реплік агента, spine пише `'spine'` обом. **Авторство береться з
`agent_spoken`** — читання з `mode` було багом, полагодженим 15.08.

## Бюджет часу одного ходу

```
кінець мовлення → 0.8с (VAD) → ~0.4с (Groq) → 1.3с утримання
                                              (2.6с якщо думка не скінчилась)
→ ~0.9с (перша дельта DeepSeek) → ~0.2с (перший звук TTS)
```

Кожне число — константа в `config.py` (`spine_vad_stop_ms`,
`spine_turn_hold_seconds`, `spine_turn_continuation_hold_seconds`),
а реальні значення пишуться в `~/.heare/logs/turns.jsonl` на кожен хід.

## Ролі

Роль — markdown-файл у `roles/` (фронтматер: `name`, `channel`,
`deny_tools`, `artifact`, `triggers`). Три канали:

- **voice** — звичайна розмова в характері ролі (вчитель, інтерв'юер);
- **log** — мовчить, записує всіх, наприкінці віддає артефакт (мітинг);
- **hints** — мовчить, кожну почуту репліку перетворює на підказку в
  дашборд (суфлер на реальній співбесіді).

Сесія починається тригер-фразою, закінчується «закінчили», віддає
markdown-артефакт у `workspace/artifacts/` і озвучений підсумок.

## Що лишилось від старого двигуна

`src/pipeline/` (pipecat, ~30 стадій) і `src/core/` живі в репозиторії
як шлях відкоту: `engine = "pipecat"` у `config.toml` + рестарт. Двигун
spine від них не залежить. Видалення — окремий крок після кількох днів
життя на новому.

Мертві важелі дашборда, які ще не проведені до нового двигуна (провайдер,
модель, панель агентів, редактор промптів), перелічені в
[findings/spine-controls.md](findings/spine-controls.md).

## Чому так

- [findings/two-agents.md](findings/two-agents.md) — чому голос і руки розділені
- [findings/two-clocks.md](findings/two-clocks.md) — чому хід закінчують слова, а не таймер
- [findings/echo-cancellation.md](findings/echo-cancellation.md) — чому AEC довго не працював
- [findings/measuring.md](findings/measuring.md) — чому без інструментів баги живуть місяцями
- [findings/known-broken.md](findings/known-broken.md) — що ще зламано
