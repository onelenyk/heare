# Розробка heare

З клону репозиторію, з Python 3.11+ і [uv](https://docs.astral.sh/uv/)
(на маку для голосу ще `brew install portaudio`):

```bash
uv sync
make brain        # мозок і його пульт на 127.0.0.1:9790
make chat         # розмова в терміналі
make voice        # мова туди й назад

make check        # лінт, 1471 тестів tests/heare, 658 тестів прошивки, каталог мов
make live         # check, зібрати й поставити застосунок, запустити, дочекатися частин
make rollback     # назад до попереднього релізу
make e2e-dist     # встановлення, оновлення й видалення в пісочниці поруч із живим
make soak N=…     # іспит годинника: N кіл (типово 1)
make bench        # стенд рук і тіл (tests/heare/test_bench.py)
make eval-route   # який виконавець дістає фразу: живий heare, справжня модель
make i18n         # зібрати каталог мов; make i18n-check — перевірити
make prompt       # системний промпт, який живий мозок дає моделі зараз
make latency      # скільки триває голосовий хід: з журналу, безкоштовно
make release V=x.y.z   # одна версія всюди, коміт і тег локально
```

Тег `v*`, відправлений на GitHub, змушує CI зібрати застосунки й колеса та
опублікувати реліз.

Станом на 28 жовтня: 1471 тестів у `tests/heare` і 658 у `tests/firmware`;
`make check` додає лінт і перевірку каталогу мов.

Застосунок для мака — у `apps/mac` (Swift); `make app` збирає його,
`make live` ставить.

## Де що

- [AGENTS.md](../AGENTS.md) — форма коду і його правила;
- [product/README.md](product/README.md) — план;
- [laws.md](laws.md) — закони, за якими heare працює;
- [README.md](README.md) — як влаштована документація.

## Якщо heare ставили по-старому

Якщо heare ставили через `install.sh` на маку (до застосунку), приберіть
служби й інструменти:

```bash
launchctl bootout gui/$(id -u)/com.heare.brain
launchctl bootout gui/$(id -u)/com.heare.keeper
rm ~/Library/LaunchAgents/com.heare.*.plist
uv tool list | grep '^heare' | cut -d' ' -f1 | xargs -n1 uv tool uninstall
```
