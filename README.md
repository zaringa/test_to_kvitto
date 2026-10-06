# Квитто — API платежей

Тестовое задание Junior Python / FastAPI. Python 3.12, FastAPI, Pydantic v2,
SQLAlchemy, PostgreSQL 17 и Redis 7.4. Зависимости зафиксированы в `uv.lock`.

## Устройство проекта

Приложение запускается в четырёх контейнерах:

| Контейнер | Что делает |
| --- | --- |
| `api` | Принимает HTTP-запрос, проверяет входные данные, отправляет команду через Redis и возвращает ответ клиенту. Swagger — показная часть API. |
| `worker` | Получает команду, выполняет алгоритм платежа, работает с PostgreSQL, отправляет результат через Redis. |
| `redis` | Хранит очередь команд Redis Streams и временные ответы. |
| `postgres` | Хранит тарифы, платежи и результаты обработанных команд. |

```mermaid
sequenceDiagram
    participant Client as Клиент
    participant API as FastAPI
    participant Redis
    participant Worker as Обработчик
    participant DB as PostgreSQL
    Client->>API: HTTP-запрос
    API->>Redis: Команда + request_id (Stream)
    Redis->>Worker: Команда
    Worker->>DB: Транзакция: действие + результат
    DB-->>Worker: Коммит
    Worker->>Redis: Ответ + ACK
    Redis-->>API: Результат (BLPOP)
    API-->>Client: HTTP-ответ
```

Каждый из четырёх бизнес-эндпоинтов проходит через Redis. Результат ждём в том же
HTTP-запросе: поэтому запрещённый переход статуса сразу возвращает требуемый `409`.
Ответ клиенту возвращает `api`. Для этой схемы достаточно одной PostgreSQL.

## Запуск в Docker

Нужны Docker Engine и Docker Compose v2 с поддержкой `--wait`.

```bash
cp .env.example .env
docker compose up --build --detach --wait --wait-timeout 120
```

- Swagger: <http://localhost:8000/docs>
- Проверка готовности: <http://localhost:8000/health>
- Тарифы: <http://localhost:8000/tariffs>

Команда без `.env` тоже работает с настройками по умолчанию. `API_PORT` меняет порт
API. PostgreSQL и Redis доступны внутри сети Compose. Данные сохраняются в именованных
томах; обычный `docker compose down` их сохраняет.

Если порт `8000` занят, задайте в `.env` `API_PORT=18000`; Swagger и примеры запросов
тогда доступны на `http://localhost:18000`.

Compose использует отдельные подсети `10.203.0.0/24` (приложение) и
`10.203.1.0/24` (тесты). Их можно изменить через `APP_NETWORK_SUBNET` и
`TEST_NETWORK_SUBNET`, если эти адреса уже заняты в вашей сети.

```bash
docker compose logs --follow api worker
docker compose down
```

## Запуск без Docker

Нужны Python 3.11+, локальные PostgreSQL и Redis 6.2+ (для `XAUTOCLAIM`). Все команды
Python выполняйте из корня репозитория. PostgreSQL и Redis должны работать как
локальные службы, а API и worker запускаются в двух отдельных терминалах.

### 1. Установить и запустить PostgreSQL и Redis

Пример для Ubuntu 24.04 с Python 3.12:

```bash
sudo apt update
sudo apt install -y python3 python3-venv postgresql redis-server
sudo systemctl start postgresql redis-server
pg_isready -h localhost -p 5432
redis-cli -h localhost -p 6379 ping
```

PostgreSQL должен сообщить `accepting connections`, Redis — `PONG`. Если службы уже
установлены и запущены, переходите к созданию БД. На другой ОС установите те же
зависимости её пакетным менеджером.

### 2. Создать пользователя и базы

Следующие команды выполните один раз. Если пользователь или база уже существуют,
пропустите соответствующую команду.

```bash
sudo -u postgres psql -c "CREATE USER kvitto WITH PASSWORD 'kvitto';"
sudo -u postgres createdb --owner=kvitto kvitto
sudo -u postgres createdb --owner=kvitto kvitto_test
```

`kvitto` используется приложением, `kvitto_test` — тестами. Таблицы и тарифы создаются
автоматически при старте worker. Учетные данные в примере предназначены для локальной
разработки.

Справка: [создание БД PostgreSQL](https://www.postgresql.org/docs/current/app-createdb.html),
[установка Redis на Linux](https://redis.io/docs/latest/operate/oss_and_stack/install/install-stack/apt/).

### 3. Установить зависимости Python и настроить подключения

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

Создайте `.env` по `.env.example` или дополните существующий файл этими настройками:

```dotenv
DATABASE_URL=postgresql+asyncpg://kvitto:kvitto@localhost:5432/kvitto
REDIS_URL=redis://localhost:6379/0
WEBHOOK_SECRET=
```

Если нужны только API и worker, установите `requirements.txt` вместо
`requirements-dev.txt`. Последний дополнительно содержит pytest, httpx и Ruff.

### 4. Запустить worker и API

В первом терминале, из корня проекта:

```bash
source .venv/bin/activate
python -m app.worker
```

Во втором терминале, также из корня проекта:

```bash
source .venv/bin/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Проверка запуска:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/tariffs
```

Ожидаемый ответ `/health` — `{"status":"ok"}`, Swagger — <http://localhost:8000/docs>.
Если порт занят, замените `--port 8000` на `--port 18000` и используйте этот порт в
URL. При запуске без Docker порт задаётся аргументом Uvicorn; `API_PORT` используется
только Docker Compose. Остановить API и worker можно через `Ctrl+C` в их терминалах.

### Тесты полностью без Docker

После шагов 1–3 достаточно работающих локальных PostgreSQL и Redis и базы
`kvitto_test`. API и worker отдельно запускать для pytest не требуется: тесты сами
создают API и два обработчика.

```bash
source .venv/bin/activate
export TEST_DATABASE_URL=postgresql+asyncpg://kvitto:kvitto@localhost:5432/kvitto_test
export TEST_REDIS_URL=redis://localhost:6379/1
python -m pytest -q
ruff check .
ruff format --check .
```

Тесты очищают схему указанной тестовой БД перед запуском; имя обязано заканчиваться
на `_test`. Используйте отдельную базу, как в примере. Для Redis тесты используют
собственный префикс ключей и удаляют только свои ключи.

`TEST_DATABASE_URL` и `TEST_REDIS_URL` нужно передать через окружение терминала,
как показано выше: тестовая конфигурация не читает их из `.env`. Порты `55432` и
`56379` из раздела ниже относятся к тестовым контейнерам; локальные службы в этом
примере используют `5432` и `6379`.

Для быстрой проверки только расчётов скидки и рассрочки достаточно зависимостей Python;
службы PostgreSQL и Redis запускать не требуется:

```bash
python -m pytest tests/test_domain.py -q
```

## Примеры запросов

Получение тарифов:

```bash
curl http://localhost:8000/tariffs
```

Платёж по standard, промокод и рассрочка на три месяца:

```bash
curl -i http://localhost:8000/payments \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: course-order-001' \
  -d '{"tariff_id":"standard","email":"student@example.com","method":"installment","installment_months":3,"promo_code":"kvitto10"}'
```

Первый запрос вернёт `201`, повтор с этим ключом — `200` и тот же платёж.
Сумма к оплате — `1791000`, скидка — `199000`, график — `[597000,597000,597000]`.
`discount` означает размер скидки в копейках, `amount` — итоговую сумму после скидки.

Подставьте `id` из ответа вместо `PAYMENT_ID`:

```bash
curl http://localhost:8000/payments/PAYMENT_ID

curl -i http://localhost:8000/webhooks/bank \
  -H 'Content-Type: application/json' \
  -d '{"payment_id":"PAYMENT_ID","status":"succeeded"}'
```

Успешный вебхук вернёт `200 {"result":"ok"}`. Повтор этого HTTP-вебхука уже пытается
выполнить `succeeded → succeeded` и вернёт `409 {"error":"invalid_transition"}`.

## Правила и ошибки

- Тарифы создаются при старте worker: `basic = 990000`, `standard = 1990000`,
  `premium = 2990000`. Все суммы в API и БД — целые копейки.
- `KVITTO10` даёт скидку 10%, регистр не важен. Неизвестный код — стандартный `422`.
- Способы оплаты: `card`, `sbp`, `installment`. Для рассрочки нужен целочисленный
  `installment_months`: `3`, `6`, `12`. Для остальных способов поле должно быть `null`
  или отсутствовать; `schedule` в ответе равен `null`.
- Остаток от деления суммы распределяется по одной копейке в первые платежи.
  Например, `1990000 / 3` даёт `[663334,663333,663333]`.
- Разрешены только `pending → succeeded`, `pending → failed`, `succeeded → refunded`.
  Запрещённый переход не меняет статус, ответ — `409 {"error":"invalid_transition"}`.
- Несуществующий платёж или тариф — `404`. Некорректные входные данные — стандартный
  ответ FastAPI `422`.
- `Idempotency-Key` — непустая строка до 200 символов. Ключ глобален для создания
  платежа. Новый валидный запрос с уже использованным ключом возвращает этот платёж,
  даже если поля нового запроса отличаются. Запросы без ключа создают новые платежи.
- Ошибка соединения с Redis — `503`, отсутствие ответа worker за отведённое время —
  `504`. Таймаут не отменяет уже отправленную команду: для повторной попытки создания
  платежа используйте тот же `Idempotency-Key`.

## Надёжность очереди

Worker использует consumer group Redis Streams. Команда подтверждается после коммита
БД и записи ответа в Redis. Незавершённые команды другой consumer можно забрать через
`XAUTOCLAIM`; период ожидания по умолчанию — 5 секунд.

`request_id` — внутренний UUID одной команды. Её результат хранится в таблице
`processed_commands` в той же транзакции, что и изменение платежа. Если worker упадёт
после коммита, повторная доставка вернёт сохранённый результат. Это работает и для
платежей без пользовательского ключа, и для вебхуков.

`Idempotency-Key` объединяет отдельные HTTP-запросы на создание платежа. Уникальное
ограничение в PostgreSQL защищает от дублей при нескольких worker. Блокировка
`SELECT ... FOR UPDATE` защищает смену статуса от одновременных вебхуков.

Ответы Redis удаляются после чтения или по TTL 60 секунд. Обработанные сообщения
удаляются из Stream, незавершённые сохраняются. Результаты в PostgreSQL сохраняются
без автоматической очистки. Redis использует AOF с `appendfsync always` и отдельный том.

Первый запуск создаёт схему через SQLAlchemy `create_all`. Миграции Alembic в это
решение не включены, как допускает задание.

Справка: [Redis Streams](https://redis.io/docs/latest/develop/data-types/streams/),
[XAUTOCLAIM](https://redis.io/docs/latest/commands/xautoclaim/),
[PostgreSQL ON CONFLICT в SQLAlchemy](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#insert-on-conflict-upsert).

## Подпись банка — дополнительная возможность

Пустой `WEBHOOK_SECRET` отключает проверку. Чтобы включить её, задайте секрет в `.env`
и пересоздайте API командой `docker compose up --detach --wait`.

Банк должен передать `X-Signature`: hex-строку HMAC-SHA256 от **исходных байтов тела**
с этим секретом. Отсутствующая или неверная подпись — `401`, платёж не меняется.
В тестах есть пример вычисления подписи: `tests/test_webhooks.py`.

## Тесты и CI

Все тесты в контейнере, включая интеграционные с настоящими PostgreSQL и Redis:

```bash
docker compose -f compose.test.yaml run --build --rm tests
docker compose -f compose.test.yaml down
```

У тестовой конфигурации отдельная сеть и БД `kvitto_test`. Рабочие тома не используются.
Интеграционные тесты запускают API и два worker в процессе pytest, а PostgreSQL и Redis
работают в отдельных контейнерах. В CI также проверяется запуск всех четырёх контейнеров
приложения и HTTP-запрос к развёрнутому API.

Для разработки нужен Python 3.11+ и [uv](https://docs.astral.sh/uv/):

```bash
uv sync --frozen
docker compose -f compose.test.yaml up --detach --wait postgres redis
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
docker compose -f compose.test.yaml down
```

Можно установить зависимости обычным pip. `requirements.txt` содержит зависимости
приложения, `requirements-dev.txt` — те же зависимости плюс pytest, httpx и Ruff.
Оба файла экспортированы из `uv.lock` с точными версиями.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
docker compose -f compose.test.yaml up --detach --wait postgres redis
python -m pytest -q
ruff check .
ruff format --check .
docker compose -f compose.test.yaml down
```

Для установки только приложения: `python -m pip install -r requirements.txt`.
Docker продолжает использовать `uv.lock`. При изменении зависимостей сначала
обновите lock-файл через `uv lock`, затем повторите экспорт:

```bash
uv export --frozen --no-dev --no-hashes --no-emit-project --no-annotate --output-file requirements.txt
uv export --frozen --no-hashes --no-emit-project --no-annotate --output-file requirements-dev.txt
```

Тесты по умолчанию подключаются к PostgreSQL на `localhost:55432`, Redis на
`localhost:56379`. Можно задать `TEST_DATABASE_URL` и `TEST_REDIS_URL`. Имя тестовой БД
обязано заканчиваться на `_test`: тесты очищают её схему перед запуском.

GitHub Actions (`.github/workflows/ci.yml`) запускается при каждом `push` и
`pull_request`: Ruff, проверка форматирования, весь pytest, сборка и запуск приложения.

## Доставка Docker-образа (CD)

После успешного CI для push в `main` job `publish` собирает runtime-образ и публикует
его в GitHub Container Registry. Имя — `ghcr.io/<владелец>/<репозиторий>` в нижнем
регистре. Публикуются два тега: полный SHA коммита и `latest`. Для воспроизводимого
деплоя выбирайте тег с SHA. Один образ используется API и worker, команды запуска
для этих сервисов различаются.

Авторизация использует встроенный `GITHUB_TOKEN` с правом `packages: write`;
дополнительный пароль Docker Hub не нужен. Организация должна разрешать публикацию
пакетов через Actions. Для private-пакета серверу потребуется право на чтение GHCR.
Подробнее: [документация GitHub](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images).

Это автоматическая доставка образа. Для автоматического развёртывания нужно указать
VPS или платформу, способ подключения и настройки окружения. Текущий workflow
сервер не обновляет. Успешный CI подтверждает пройденные проверки, а запуск на сервере
проверяется отдельно при развёртывании.

База контейнера приложения — официальный `python:3.12-slim`, содержащий Python и
минимальные необходимые пакеты Debian. GitHub Actions выполняется на `ubuntu-latest`.
Docker-сервер также может работать на Ubuntu: контейнеру не нужна такая же ОС,
как у сервера. [Описание Python-образа](https://hub.docker.com/_/python).

## Как разобраться в коде

Рекомендуемый порядок чтения:

1. `app/schemas.py` — поля запросов, ответов и правила валидации.
2. `app/domain.py` — скидка, рассрочка и допустимые переходы. Эти функции не зависят от БД.
3. `app/models.py` — три таблицы и ограничения PostgreSQL.
4. `app/service.py` — создание платежа, смена статуса, транзакция и защита от повторов.
5. `app/broker.py` — отправка команды и получение ответа через Redis.
6. `app/worker.py` — чтение очереди, обработка и восстановление команд.
7. `app/main.py` — HTTP-эндпоинты и перевод результата worker в ответ клиенту.
8. `tests/` — примеры нормальных запросов, ошибок и одновременной обработки.

`app/config.py` содержит настройки, `app/database.py` — подключение и начальные тарифы.
История запросов к ассистенту и замеченные слабые решения записаны в `AI_LOG.md`.
