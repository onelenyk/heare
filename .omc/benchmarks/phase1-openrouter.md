# Phase 1 — OpenRouter streaming TTFT benchmark

Model: `google/gemini-3.1-flash-lite-preview-20260303`
Prompt: `Коротко скажи привіт українською. Одне речення.`
Requests: 10 sequential

## Results

- First TTFT: 1447 ms
- Warm TTFT median (runs 2+): 1131 ms
- All TTFT median: 1165 ms
- Total median: 1170 ms

Raw TTFTs (ms): `[1447, 938, 1022, 1081, 1131, 1052, 1237, 1199, 7328, 1450]`
Raw totals (ms): `[1451, 940, 1023, 1082, 1132, 1053, 1238, 1208, 7361, 1455]`

Sample reply: `Привіт!`

## Verdict

**GO (marginal)** — warm TTFT median 1131ms — within 1.5s tolerance
