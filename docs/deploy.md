# Доставка heare: клієнти й сервер

*15 листопада 2026. Замінює systemd-модель сервера; історію рішень див. у
[product/delivery.md](product/delivery.md).*

Це головний документ про те, **як зміна в репозиторії стає тим, що працює
в людини**. Для агентів: усе, що треба знати, щоб не зламати доставку.

## 0. Ранбук: що робити агенту

**Доступи (не в репо):**
- Coolify API: на міні-ПК `~/.coolify-token` (0600); той самий токен лежить
  в GitHub secrets як `COOLIFY_API_TOKEN`.
- Cloudflare API (DNS, тунель, Access): на міні-ПК `~/.cf-api-token` (0600).
- Міні-ПК: `ssh nazar@192.168.110.13` (лише LAN; публічної адреси немає).
- GitHub secrets: `COOLIFY_URL`, `COOLIFY_API_TOKEN`, `CF_ACCESS_CLIENT_ID`,
  `CF_ACCESS_CLIENT_SECRET`, `COOLIFY_PROD_UUID`, `COOLIFY_DEV_UUID`.

**Реліз клієнтів (dmg/APK/install.sh/колеса):**

```bash
git checkout main && git pull
make release V=x.y.z          # версія всюди + check + коміт + тег
git push origin main vX.Y.Z   # це публікація; CI збере реліз
gh run watch $(gh run list --branch vX.Y.Z --limit 1 --json databaseId -q '.[0].databaseId')
```

**Реліз сервера:**

- dev: `git push origin develop` → образ `:dev` → деплой у dev;
- прод: мердж у `main` і `git push origin main` → образ `:stable`/`:sha` →
  деплой у прод.

Обидва видно у `gh run list --branch main` (або `develop`); job `Deploy`.

**Перевірка після деплою:**

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://brain.onelenyk.dev/health   # 200
curl -s -o /dev/null -w '%{http_code}\n' https://dev-brain.onelenyk.dev/health
gh run view <id> --json jobs -q '.jobs[] | "\(.name): \(.conclusion)"'
```

**Відкат:**

- один деплой: `GET /api/v1/applications/{uuid}/rollback-images` → вибрати
  попередній `sha-…` → `POST …/rollback {"commit":"sha-…"}` (Coolify API;
  curl — з `--http1.1 -A heare-ci/1.0`);
- повністю на systemd: зупинити `heare-prod` у Coolify, потім
  `systemctl --user enable --now heare-brain heare-keeper` на міні-ПК.

**Не робити:**

- не запускати `install.sh` на сервері — оновлення тільки образом;
- не ставити тег вручну без `make release` (версії в пакетах розійдуться);
- не міняти тег образу в Coolify руками — це робить deploy-job (`sha-…`);
- не комітити токени, `~/.heare`, логи;
- не плутати «реліз» клієнтів (тег) із «деплоєм» сервера (мердж у `main`).

Якщо після пушу в `gh run list` немає нового запуску — це нормально, коли
змінилися лише `docs/**` або `.md`: такий пуш не збирає й не деплоїть нічого.

## 1. Два треки

| Трек | Що це | Артефакт | Куди |
|---|---|---|---|
| **Клієнтський** | мак, телефон, Linux-тіло | dmg ×2, APK, `install.sh`, колеса | GitHub Release за тегом |
| **Серверний** | мозок (і частини, що живуть без заліза) | образ `ghcr.io/onelenyk/heare-brain` | Coolify: pull + деплой |

Клієнт оновлюється своїм шляхом (застосунок мака — `Updater`, Linux-тіло —
`install.sh`/наглядач). Сервер оновлюється **тільки** новим образом: у
контейнері наглядач не ставить `install.sh` (`HEARE_NO_SELF_UPDATE=1`).

## 2. Версія й тег

Одна версія на всі 12 пакетів. Реліз клієнтів:

```bash
make release V=0.9.22     # версія всюди + uv lock + make check + коміт + тег
git push origin main v0.9.22
```

`make release` нічого не пушить. Пуш тега — це публікація: CI збере
колеса, два dmg, APK (якщо підписаний), `install.sh`, `SHA256SUMS.txt`,
образ `:v0.9.22` і `:latest`, і створить GitHub Release.

## 3. Гілки → образи → середовище

| Подія | Теги образу | Середовище | Деплой |
|---|---|---|---|
| push у `develop` | `sha-<12>`, `:dev` | dev (`dev-brain.onelenyk.dev`) | так, автоматично |
| push у `main` | `sha-<12>`, `:stable` | прод (`brain.onelenyk.dev`) | так, автоматично |
| тег `vX.Y.Z` | `sha-<12>`, `:vX.Y.Z`, `:latest` | — | ні (це клієнтський реліз) |
| PR у `main`/`develop` | — | — | ні (лише перевірки) |
| `workflow_dispatch` | `sha-<12>` (+ `:stable`/`:dev` за гілкою) | — | ні |

Правило: **main — це прод**. Мердж у `main` = виїзд у прод. Нічого не
деплоїться з тега і нічого не деплоїться з PR.

## 4. Що робить CI

`.github/workflows/ci.yml`, jobs:

1. **Lint** — `ruff` (ubuntu).
2. **Test (macOS)** — `pytest tests/heare tests/firmware`, coverage ≥ 80%,
   `mypy`. Флейкі-тест ретраїться один раз (`--last-failed`) з warning.
3. **Build wheels** — `uv build --all-packages`, артефакт для релізу.
4. **Phone APK** — debug завжди, release — лише з keystore-секретами.
5. **Server image** — `docker build -f docker/server.Dockerfile`, smoke
   (`/health` = 200 у контейнері), push у GHCR з тегами вище; кеш buildx.
6. **Deploy (dev|prod)** — лише push у `develop`/`main`:
   - пінить у Coolify точний `sha-<12>` цього коміту;
   - `POST /api/v1/deploy` (через Cloudflare Access);
   - чекає `finished` (до 15 хв);
   - перевіряє `/health` через публічні двері (до 3 хв);
   - якщо не відповіло — `rollback` на попередній `sha-…`.
7. **Mac app** — лише тег/dispatch: два dmg на двох архітектурах.
8. **Release** — лише тег, після всіх: суми + `gh release create`.

## 5. Серверний образ

`docker/server.Dockerfile` — один контейнер:

- **brain** (порт 9790), **keeper**, **native-hands**, **sources**;
- supervisord як PID 1 (brain, потім keeper), логи в stdout;
- non-root uid 1000, том `/home/heare/.heare`, `HEALTHCHECK` на `/health`;
- `HEARE_NO_SELF_UPDATE=1`, `update_check=false` — оновлення тільки образом;
- entrypoint: `api_host=0.0.0.0`, `api_port=9790`, `role=all`, стале ім'я
  машини (`HEARE_MACHINE_NAME`, типово `heare`), trusted-список для
  вбудованих частин.

Локально:

```bash
docker build -f docker/server.Dockerfile -t heare-server .
docker run -p 127.0.0.1:8095:9790 -v heare:/home/heare/.heare heare-server
```

Голос і очі в образ не входять: мікрофон/екран — це клієнт. `cli-hands`
(opencode) теж не входять — нативні руки мають їх замінити.

## 6. Coolify

Проєкт **heare** (`p73mjdqx0tgdqhqvdq2jau9n`), сервер `localhost`
(`wcg08000g0cksogocs00coc4`).

| | prod | dev |
|---|---|---|
| застосунок | `heare-prod` (`keba6tvapqwslfem94gbg4jh`) | `heare-dev` (`dnki3wefued3pefdcxoc5hxl`) |
| образ | `sha-<commit>` (пін на деплой) | `sha-<commit>` |
| порт | `8095:9790` (0.0.0.0) | `8101:9790` (0.0.0.0) |
| том | `keba…-heare-prod-data` | `dnki…-heare-dev-data` |
| healthcheck | `/health`, 25 с старт, 5 спроб | те саме |
| двері | `brain.onelenyk.dev` | `dev-brain.onelenyk.dev` |

API Coolify назовні — `coolify.onelenyk.dev` (тільки service token; люди
заходять у панель по LAN: `http://192.168.110.13:8000`).

Секрети GitHub: `COOLIFY_URL`, `COOLIFY_API_TOKEN`,
`CF_ACCESS_CLIENT_ID`, `CF_ACCESS_CLIENT_SECRET`, `COOLIFY_PROD_UUID`,
`COOLIFY_DEV_UUID`.

Старий застосунок `heare` (id 13, збірка dockerfile, том від 2–3 листопада)
— застарілий, лишається лише як історія; не деплоїти.

## 7. Cloudflare

Тунель `7115a203-815a-4572-b4f9-0176747d3e30` (`0_len_server`), **remote
config** — маршрути змінюються через API/дашборд, не файлом на машині.

| hostname | origin | Access |
|---|---|---|
| `brain.onelenyk.dev` | `http://192.168.110.13:8095` | сторінка — email власника; `/health`, `/api` — bypass |
| `dev-brain.onelenyk.dev` | `http://192.168.110.13:8101` | те саме |
| `coolify.onelenyk.dev` | `http://192.168.110.13:8000` | тільки service token |

**Origin — LAN IP, не 127.0.0.1.** Тунель має два конектори (systemd і
контейнер `cloudflared-web`); контейнерний не бачить host-loopback, тому
loopback-origin дає 502.

**CI і WAF.** Cloudflare ріже GitHub-раннерів на дефолтному `curl` (403 без
тіла). Тому кожен виклик у deploy-job: `curl -sS --http1.1 -A "heare-ci/1.0"`.

## 8. Дані, ключі, копії

- **Прод-дані:** том `keba…-heare-prod-data`, у нього скопійовано `~/.heare`
  на момент переїзду (15.11). Оригінал `~/.heare` на хості не чіпано.
- **Ключі:** у томі (`machine/keys.json`, 0600) або env
  `DEEPSEEK_API_KEY`/`GROQ_API_KEY`.
- **Ім'я машини:** файл `machine/machine_name` (`0lens`); entrypoint пише
  його, лише якщо файлу немає.
- **Копії:** щоденний `heare-backup.sh` (cron 04:30) + tar перед переїздом
  `~/backups/heare/before-container-*.tgz`.

## 9. Відкат

1. **Деплой:** Coolify UI → попередній деплой → Redeploy, або API
   `POST /api/v1/applications/{uuid}/rollback {"commit":"sha-…"}`
   (список: `GET …/rollback-images`). CI робить це сам при невдалому health.
2. **Повний відкат на systemd** (юніти вимкнені, не видалені):

   ```bash
   # зупинити контейнер heare-prod у Coolify, потім:
   systemctl --user enable --now heare-brain heare-keeper
   ```

   Дані systemd — в оригінальному `~/.heare` (стан на 15.11).

## 10. Границі

- З мапінгом портів Coolify робить stop→start: **кількасекундна пауза**,
  health-gate робить CI, не Coolify.
- `sha-…` теги обов'язкові: `:dev`/`:stable` рухаються, відкат на рухомий тег
  нічого не повертає.
- Піддомен рівня `dev.brain.…` не має універсального сертифіката — тільки
  однорівневий `dev-brain.…`.
- Клієнти (мак/APK) не оновлюються цим конвеєром; їхній шлях — Release і
  пульт.
