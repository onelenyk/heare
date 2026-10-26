# heare на сервері

Мозок — пам'ять і розмова — може жити не на маку, а на Linux-машині, що
завжди ввімкнена, або в Docker. Мікрофон йому не потрібен: голос і руки
лишаються на маку й під'єднуються до мозку як частини.

## Linux

```bash
curl -sSL https://raw.githubusercontent.com/onelenyk/heare/v0.2.0/install.sh | sh -s -- --role server --from https://github.com/onelenyk/heare/releases/download/v0.2.0 --version 0.2.0
```

Що робить:
- ставить [uv](https://docs.astral.sh/uv/), якщо його немає;
- кладе кожну частину, яку ця система вміє запускати, в окреме середовище;
- тримає мозок і наглядача живими через systemd.

Які частини працюють — перемикач на сторінці налаштування,
<http://127.0.0.1:9791>. На сервері до неї дістаєшся тунелем:
`ssh -L 9791:127.0.0.1:9791 user@server`. Пульт — на
<http://127.0.0.1:9790/>.

Інші ролі:
- `--role body --url URL --code CODE` — ця машина лише з частинами,
  спарена з мозком деінде (код — зі сторінки того мозку);
- `--parts voice,hands` — увімкнути частини одразу;
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

Лише мозок, стан у томі, ключі зі змінних середовища:

```bash
DEEPSEEK_API_KEY=… docker compose -f docker/compose.yaml up -d --build
docker compose -f docker/compose.yaml exec brain heare-brain code   # спарити браузер
```

Більше — у [docker/](../docker/).
