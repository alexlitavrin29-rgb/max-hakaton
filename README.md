# Точка опоры

Помощник в MAX для детей и молодых людей, которые живут или жили в центрах
содействия семейному воспитанию. Справочные маршруты по документам, учёбе,
жилью и выплатам, поиск работы, аренды и социальной помощи, сохранение и шеринг.

**[Открыть бота в MAX](https://max.ru/t143_hakaton_max_bot)** → «Начать» или `/start`.
Кнопка «Поехали» открывает разделы для детей и взрослых выпускников.
«Ищу работу» и «Ищу жильё» открывают справочные разделы, вложенные кнопки
«Поиск работы» / «Поиск жилья» открывают приложение. «Где могут помочь» ведёт
в приложение сразу. В конце ответа доступны «Назад» и «К оглавлению».

## Комплект сдачи

| Материал | Файл / адрес |
|---|---|
| Публичный API | https://135-106-229-210.sslip.io |
| Репозиторий GitHub | https://github.com/alexlitavrin29-rgb/max-hakaton |
| Полный архив в GitVerse | https://gitverse.ru/aleksandr29/MAX-HAKATON |
| Проверка доступности | https://135-106-229-210.sslip.io/api/health |
| HTTP-проверки | [DATA-API.yaml](DATA-API.yaml) |
| OpenAPI 3.1 | [openapi.yaml](openapi.yaml) |
| Docker для чистой базы | [submission/compose.yaml](submission/compose.yaml) |
| Развёртывание на VPS | [project/deploy/README.md](project/deploy/README.md) |
| Зависимости | [приложение](project/llm/requirements-bot.txt), [API](project/admin/requirements.txt), [тесты](requirements-dev.txt), [валидаторы](requirements-validation.txt) |
| Доступ и тестовые данные | [ACCESS.md](docs/submission/ACCESS.md) |
| Архитектура и данные | [TECHNICAL.md](docs/submission/TECHNICAL.md) |
| Хранение и удаление данных | [DATA-POLICY.md](docs/submission/DATA-POLICY.md) |
| Результаты проверок | [VERIFICATION.md](docs/submission/VERIFICATION.md) |
| Нагрузочный прогон | [LOAD.md](docs/submission/LOAD.md) |
| Продуктовая оценка и показ | [PRODUCT.md](docs/submission/PRODUCT.md) |
| Что дополнить | [MISSING.md](docs/submission/MISSING.md) |
| Презентация | [PDF](submission/presentation.pdf), [PPTX](submission/presentation.pptx) |

Выпуск r5 установлен на VPS: bot и miniapp здоровы, `/api/health` вернул 200,
опубликованный сценарий MAX остался версией 9 с `child_only=true`.
Обычный браузер открывает оболочку, защищённый сеанс начинается из MAX.
Владелец подтвердил работу приложения на настоящих аккаунтах MAX.
Резервные копии с дампами на VPS хранятся 30 суток; подробности — в
[политике данных](docs/submission/DATA-POLICY.md).

## Локальный запуск

Требуются Docker с Compose v2 и интернет для сборки. Из корня комплекта:

```sh
cp submission/.env.example submission/.env
# Замените POSTGRES_PASSWORD на своё значение.
docker compose --env-file submission/.env -f submission/compose.yaml up -d --build
docker compose --env-file submission/.env -f submission/compose.yaml ps
```

В PowerShell можно использовать `Copy-Item` вместо `cp`.
Откройте http://127.0.0.1:18866. Это локальный предпросмотр на loopback.
Он позволяет проверить помощь, интерфейс и сохранения без аккаунта MAX.
Не публикуйте preview наружу. Для живого поиска нужны доступные поставщики
и ключи из `submission/.env.example`.

`seed` устанавливает снимок опубликованного сценария версии 9 в новую базу.
При существующем сценарии загрузка ничего не изменяет. Пользовательских данных
в снимке нет. Бот по умолчанию выключен, сообщения MAX не отправляются.
Для отдельного тестового бота задайте его токен и включите профиль `--profile bot`.
Не запускайте второй polling-процесс с токеном уже работающего бота.

Остановка с сохранением базы:

```sh
docker compose --env-file submission/.env -f submission/compose.yaml stop
```

Повторный запуск с сохранённой базой:

```sh
docker compose --env-file submission/.env -f submission/compose.yaml up -d
```

### Компоненты, окружение и данные

Локально запускаются PostgreSQL 16, одноразовый загрузчик `seed` и FastAPI
miniapp с общим движком `FlowDialogue`. Интерфейс — HTML/CSS/JavaScript.
Профиль `bot` добавляет MAX polling; редактор не нужен для проверки готового
сценария. Подробная схема — в [TECHNICAL.md](docs/submission/TECHNICAL.md).
Порт `127.0.0.1:18866` направлен на `8766` miniapp; PostgreSQL `5432` доступен
только внутри сети Compose. Порт `18867` нужен только проверке второго экземпляра.

Параметры задаются в `submission/.env`:

| Переменная | Назначение и условия проверки |
|---|---|
| `POSTGRES_PASSWORD` | Заменить пример своим локальным паролем до первого запуска |
| `MAX_BOT_TOKEN` | Для preview оставить фиктивное значение из примера; рабочий токен нужен только своему тестовому боту |
| `MINIAPP_BOT_NAME` | Имя бота для ссылок MAX |
| `POLZA_API_KEY` | Необязательный ключ LLM Polza для разбора свободного текста |
| `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS` | Провайдер, адрес, модель и тайм-аут LLM; значения приведены в примере |
| `HOUSING_API_KEY` | Ключ ReefAPI для живого поиска аренды |
| `HH_ACCESS_TOKEN`, `HH_USER_AGENT` | Токен приложения HeadHunter и идентификатор клиента для живого поиска вакансий |

Compose задаёт адрес и имя PostgreSQL, `MINIAPP_PREVIEW=1`, локальный адрес
каталога помощи и пути кэширования. MAX, LLM, HeadHunter, «Работа России» и
ReefAPI — внешние сервисы: они не воспроизводятся внутри Docker. Для MAX нужен
отдельный аккаунт и тестовый бот; для поставщиков — доступ в интернет и,
где указано выше, действующий ключ. «Работа России» используется без ключа.
Отказ поставщика может дать уведомление или частичную выдачу; наличие объявлений
не гарантируется. Проверка помощи и сохранений ниже не требует этих сервисов.

Снимок `submission/scenario.json` и каталог `project/llm/data/help_points.json`
служат тестовыми данными. PostgreSQL в томе `submission_db` хранит сценарий,
сеансы и сохранения. Повторный seed не заменяет существующий сценарий.
Preview использует общую тестовую личность; реальные пользовательские данные
для проверки не нужны. Условия хранения и удаления — в
[DATA-POLICY.md](docs/submission/DATA-POLICY.md).

### Пошаговая локальная проверка

1. Выполнить команду запуска выше. `seed` должен завершиться с кодом 0,
   `postgres` и `miniapp` — перейти в healthy.
2. Открыть http://127.0.0.1:18866/api/health — ожидается `{"status":"ok"}`.
3. Открыть http://127.0.0.1:18866, выбрать помощь, ввести «Казань»,
   выбрать «Город Казань», затем «Еда». Ожидается карточка с условиями обращения.
4. Сохранить карточку сердечком и открыть «Моё»: карточка должна появиться.
   Вернуться на главную: выбранная карточка и условия поиска сохраняются.
5. Остановить и повторно запустить Compose командами выше. Сохранение должно
   остаться в «Моём». На локальном preview удалить тестовые данные кнопкой
   «Удалить сохранения и напоминания»: приложение предложит открыть новый сеанс.

## Одна команда проверки выпуска

После настройки `submission/.env` и установки Docker, Python, Node.js
подготовьте Python-зависимости проверок и запустите из корня репозитория
(npm-зависимости и Chromium команда установит сама):

```sh
python -m pip install -r requirements-validation.txt httpx==0.28.1
python tools/submission/verify_release.py
```

Команда создаёт отдельную PostgreSQL, выполняет полный Python-набор без
пропусков, Node и браузерные тесты, проверки повторов между двумя экземплярами
и после рестарта, валидаторы, затем собирает ZIP и Git bundle. Проверка ZIP
включает манифест, CRC и поиск локально известных секретов. Итоговый путь и
точные числа тестов команда печатает в конце. Не запускайте второй preview на
127.0.0.1:18866/18867 одновременно с ней. Повторяемые тесты и скрипты проверки
остаются в `project/*/tests` и `tools/submission`.

Команда создаёт в `dist/` чистый Git-репозиторий с одним commit, ZIP, bundle
и receipt с SHA-256. Bundle можно клонировать командой
`git clone --branch submission ПУТЬ-К-BUNDLE tochka-opory`; проверка выпуска сама делает пробный
клон и сверяет его манифест. Рабочий каталог разработки содержит исторические
файлы и локальные данные; публикуется только чистый репозиторий из `dist/`.
В GitHub не включён исходный журнал `docs/quality-acceptance/sealed-2026-09-26/work.jsonl`
размером 156 МБ. Пять replay-тестов используют уже включённые в комплект
`sealed_turns` из `repair-packet-private.json`; тесты не удалены и не пропущены.
Полный журнал сохранён в выпуске GitVerse.

## Отдельные проверки

```sh
python -m pip install -r requirements-validation.txt
python tools/data-api/validate_data_api.py DATA-API.yaml --schema tools/data-api/DATA-API.schema.json --openapi openapi.yaml
python -m openapi_spec_validator openapi.yaml
python tools/submission/check_api.py --output api-check-results.json
```

Эталонный валидатор проверяет структуру и пути OpenAPI, но не выполняет HTTP.
Последняя команда проверяет публичные операции. Для полного прогона нужен
тестовый сеанс: см. ACCESS.md. Пропуски защищённых проверок явно отражены в отчёте.

```sh
docker compose --env-file submission/.env -f submission/compose.yaml run --rm --no-deps -e HELP_API_URL= -e HELP_CITY_FIRST= -e TRUDVSEM_CACHE_PATH= -e REEF_CACHE_PATH= miniapp python -m pytest -q -p no:cacheprovider project/llm/tests project/admin/tests project/miniapp/tests --basetemp=/tmp/submission-tests
node --test project/miniapp/tests/network.test.cjs
```

Переменные после `-e` сбрасывают только настройки сервиса в тестовом контейнере:
иначе его тесты обращаются к localhost другого контейнера и разделяют живой
файловый кэш поиска. Работающий miniapp продолжает использовать настройки Compose.

Текущий replay-контроль публикации 9 использует версионированный эталон
`replay_publication9_v1.json` и входит в общий прогон. Исторический replay
и его исходные записи остаются в рабочей истории проекта.

## Структура

```text
project/llm/        общий движок, MAX, поисковые интеграции и данные
project/miniapp/    публичный API, MAX-авторизация, интерфейс, сохранения
project/admin/      редактор сценария и его тесты
submission/         Compose, Dockerfile, снимок сценария, презентация
 tools/data-api/    неизменённый валидатор организаторов
 tools/submission/  проверки, экспорт OpenAPI и сборка комплекта
docs/submission/    актуальная документация сдачи
```

Для сдачи используйте документы `docs/submission/`. Исторические отчёты рабочего
каталога отражают разные этапы разработки. Каталог содержит 364 карточки помощи
с условиями аудитории, реестр сохранения — 117 ответов. Эти числа не означают,
что каждая услуга доступна любому пользователю.
