# heare на сервері

Мозок — пам'ять і розмова — може жити не на маку, а на Linux-машині, що
завжди ввімкнена, або в Docker. Мікрофон йому не потрібен: голос і руки
лишаються на маку й під'єднуються до мозку як частини.

*Стан 28 жовтня 2026: Docker-мозок є в коді (`docker/brain.Dockerfile`),
`docker/compose.yaml` публікує порт на loopback. TLS, тунелю й публічної
адреси в heare немає.*

## Linux

```bash
curl -sSL https://raw.githubusercontent.com/onelenyk/heare/v0.2.0/install.sh | sh -s -- --role server --from https://github.com/onelenyk/heare/releases/download/v0.2.0 --version 0.2.0
```

У v0.3.0 канонічна назва ролі — `brain`; `server` лишається аліасом.

`brain` — канонічна назва ролі; `server` лишається синонімом (як `mac` —
синонім `all`).

Що робить:
- ставить [uv](https://docs.astral.sh/uv/), якщо його немає;
- кладе кожну частину, яку ця система вміє запускати, в окреме середовище;
- тримає мозок і наглядача живими через systemd.

Які частини працюють — перемикач на сторінці налаштування,
<http://127.0.0.1:9791>. На сервері до неї дістаєшся тунелем:
`ssh -L 9791:127.0.0.1:9791 user@server`. Пульт — на
<http://127.0.0.1:9790/>.

Інші ролі:
- `--role all` — мозок і частини на цій машині (типово на macOS);
- `--role body --url URL --code CODE` — ця машина лише з частинами,
  спарена з мозком деінде (код — зі сторінки того мозку);
- `--parts voice,eyes,sources,hands,native-hands` — увімкнути частини одразу;
- `--no-service` — нічого не запускати.

Без системного сховища ключів (на сервері) ключі лежать у
`~/.heare/machine/keys.json` з правами 0600; змінні середовища мають
перевагу.

**Оновити:** запусти ту саму команду з новим номером версії.

**Копія:** `heare-brain backup` і `heare-brain restore ФАЙЛ`.

**Видалити:**

```bash
systemctl --user disable --now heare-brain heare-keeper
rm ~/.config/systemd/user/heare-*.service
uv tool list | grep '^heare' | cut -d' ' -f1 | xargs -n1 uv tool uninstall
```

Усе, що heare пам'ятає, — у `~/.heare`. Видали теку, щоб забути все.

## Docker

Лише мозок, стан у томі, ключі зі змінних середовища. Образ збирає колеса
core/reach/memory/brain і запускає `heare-brain`; порт типово
`127.0.0.1:9790`, `HEARE_BIND` і `HEARE_PORT` міняють це. Усередині
контейнера мозок слухає `0.0.0.0`, бо інакше опублікований порт не дійде;
назовні це нічого не відкриває, поки `HEARE_BIND` не змінено. HTTPS немає:
якщо він потрібен, став TLS-проксі перед мозком.

```bash
DEEPSEEK_API_KEY=… docker compose -f docker/compose.yaml up -d --build
docker compose -f docker/compose.yaml exec brain heare-brain code   # код для браузера або тіла
```

`heare-brain code` дає 8-символьний одноразовий код (5 хв) для `/api/pair`:
браузер обмінює його на HttpOnly cookie, тіло — на токен машини, далі кожна
частина отримує свій токен у стукові. Майстер-ключ лишається на машині мозку.

Більше — у [docker/](../docker/).

## Застосунок мака

Застосунок вбудовує лише локальний мозок: `Config.brainBase` —
`http://127.0.0.1:<api_port>`. Якщо ця машина — тіло (`role = body`, `url`
заданий), «Відкрити heare» відкриває `url` у типовому браузері, а не сторінку
у вікні; мозок деінде в застосунок не вбудовується.
