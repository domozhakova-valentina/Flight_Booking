# Flight Booking

Домашняя работа: система бронирования авиабилетов из двух сервисов на Python.
Booking Service принимает REST-запросы, а Flight Service работает с рейсами и местами.
Сервисы общаются по gRPC, у каждого своя PostgreSQL. Redis используется для кеша.

```text
Клиент → Booking Service → gRPC → Flight Service
              ↓                       ↓
         Booking DB              Flight DB + Redis
```

## Запуск

Нужен запущенный Docker Desktop. Из корня проекта:

```bash
docker compose up
```

Compose собирает приложения, генерирует код из `.proto`, поднимает базы и Redis.
Миграции Alembic применяются автоматически перед запуском каждого приложения.
Устанавливать Python и базы отдельно не нужно.

Контейнеры и зависимости описаны в [docker-compose.yml](docker-compose.yml).
Сборка запускает [proto/generate.py](proto/generate.py), а при старте приложения
`main.py` вызывает `migrate()` из своего `db.py`. Alembic хранит номер применённой
миграции, поэтому повторный запуск не создаёт таблицы заново.

- REST API: http://localhost:8000
- Swagger с описанием REST-методов: http://localhost:8000/docs
- Проверка Booking Service и его БД: http://localhost:8000/health
- gRPC Flight Service: `localhost:50051`

После изменения кода: `docker compose up --build`.
Остановка: `docker compose down`. Данные PostgreSQL сохраняются в Docker-томах.
Начальные рейсы не добавляются: при пустой БД поиск возвращает `[]`.

## Структура проекта

| Путь | Содержимое |
| --- | --- |
| `services/booking/app/` | REST API, бронирования, gRPC-клиент, retry и Circuit Breaker |
| `services/flight/app/` | gRPC API, операции с рейсами и местами, кеш |
| `services/*/migrations/` | Миграции отдельных баз |
| `proto/flight.proto` | Общий gRPC-контракт |
| `redis/` | Настройки мастера, реплики и Sentinel |
| `tests/` | Проверки транзакций, аутентификации, кеша, retry и Circuit Breaker |
| [docs/architecture.md](docs/architecture.md) | ER-диаграмма, ограничения и объяснение 3NF |

В обоих сервисах код разделён на обычные модули: обработчики принимают запрос,
`service.py` выполняет операции с данными, `db.py` подключает PostgreSQL,
`config.py` читает переменные окружения. Booking не обращается к Flight DB напрямую.

## REST API

| Метод | Назначение |
| --- | --- |
| `GET /flights?origin=SVO&destination=LED&date=2030-01-01` | Поиск рейсов; маршрут обязателен, дату можно не передавать |
| `GET /flights/{id}` | Получение рейса |
| `POST /bookings` | Создание бронирования |
| `GET /bookings/{id}` | Получение бронирования |
| `GET /bookings?user_id=UUID` | Список бронирований пользователя |
| `POST /bookings/{id}/cancel` | Отмена бронирования, тело запроса не нужно |

В JSON для `POST /bookings` передаются `user_id`, `flight_id`, `passenger_name`,
`passenger_email` и `seat_count`. Идентификаторы — UUID, рейс должен существовать.
Примеры запросов доступны в Swagger.

REST-обработчики находятся в [booking/app/api.py](services/booking/app/api.py).
Здесь проверяются входные данные, формируется JSON-ответ и ошибки gRPC переводятся
в HTTP-коды: например, отсутствующий рейс даёт `404`, нехватка мест — `409`.
Вызовы Flight Service собраны в [flight_client.py](services/booking/app/flight_client.py),
а создание и отмена бронирований — в [booking/app/service.py](services/booking/app/service.py).

При создании Booking вызывает `GetFlight`, затем `ReserveSeats`. После успеха
сохраняет бронирование со статусом `CONFIRMED` и стоимостью из ответа `GetFlight`.
Если резервирование не удалось, бронирование не создаётся. Отмена вызывает
`ReleaseReservation` и меняет статус на `CANCELLED`. Повторная отмена не возвращает места дважды.

`booking_id` создаётся один раз перед резервированием. Итоговая стоимость равна
`seat_count * flight.price_kopecks` и сохраняется в Booking DB как снимок цены.
Получение и список бронирований читают только Booking DB и не вызывают Flight Service.

## gRPC и базы данных — пункты 1–4

Контракт содержит `SearchFlights`, `GetFlight`, `ReserveSeats`, `ReleaseReservation`
и `UpdateFlight`. Python-код клиента и сервера генерируется через `protoc` при сборке.
Время передаётся через `Timestamp`, статусы — через enum, деньги — целым числом копеек.
Бизнес-ошибки возвращаются стандартными кодами gRPC, например `NOT_FOUND`
для отсутствующего рейса и `RESOURCE_EXHAUSTED` при нехватке мест.

Поля запросов, ответов и статусы описаны в [flight.proto](proto/flight.proto).
Методы сервера реализованы в [flight/app/grpc_api.py](services/flight/app/grpc_api.py):
обработчик проверяет запрос, вызывает `service.py` и преобразует результат в protobuf.
SQL-запросы находятся в [flight/app/service.py](services/flight/app/service.py).

В Flight DB находятся `flights` и `seat_reservations`, в Booking DB — `bookings`.
Ограничения БД проверяют цену, количество мест, статусы, порядок времени вылета
и прилёта, уникальность номера рейса с датой UTC. Между разными БД нет внешних ключей.
Схема и объяснение 3NF находятся в ER-диаграмме по ссылке выше.
Поиск возвращает только рейсы `SCHEDULED`.

Таблицы и ограничения создаются начальными миграциями:
[Booking DB](services/booking/migrations/versions/0001_initial.py) и
[Flight DB](services/flight/migrations/versions/0001_initial.py).
В SQL используются параметры, а не подстановка пользовательских значений в строку.

`UpdateFlight` частично обновляет рейс по UUID. Непереданные поля сохраняются,
свободные места пересчитываются с учётом занятых. Уменьшить вместимость ниже
занятого количества нельзя. Стоимость уже созданных бронирований не меняется.

Обработчик `UpdateFlight` через `HasField()` определяет, какие поля были переданы.
Функция `update_flight()` блокирует рейс, объединяет изменения с текущими данными
и проверяет результат. Свободные места считаются так: новая вместимость минус
ранее занятые места. Метод доступен по gRPC, отдельного REST-маршрута для него нет.

## Транзакции — пункт 5

В Flight Service списание мест и создание резервации выполняются в одной
транзакции. Возврат мест и смена статуса резервации тоже выполняются вместе.
`SELECT FOR UPDATE` блокирует строку рейса, чтобы параллельные запросы
не могли забронировать одно последнее место.

Это функции `reserve_seats()` и `release_reservation()` в Flight `service.py`.
Блок `with engine.begin()` фиксирует изменения при успехе и откатывает при исключении.
Сначала блокируется рейс, поэтому операции с его местами выполняются по очереди.

Если после резервирования не удалось сохранить booking, Booking Service пытается
вернуть места через `ReleaseReservation`. Это компенсация, а не общая транзакция
двух БД: при падении процесса или неудачной компенсации может остаться резерв
без бронирования. Автоматическое восстановление таких случаев не реализовано.

За создание отвечает `create_booking()`, за попытку возврата при ошибке —
`_release_after_failure()` в Booking `service.py`. Ошибка компенсации попадает в лог с `booking_id`.

## API key — пункт 6

Booking передаёт ключ в gRPC metadata `x-api-key`. Общий interceptor Flight Service
проверяет его для всех пяти методов. Отсутствующий или неправильный ключ даёт
`UNAUTHENTICATED`. Оба приложения получают `GRPC_API_KEY` через окружение контейнера.

Ключ добавляется в `metadata` в Booking `flight_client.py`. Проверка — класс
`ApiKeyInterceptor` в Flight `grpc_api.py`, подключённый при запуске сервера в `main.py`.
Для REST-запросов ключ передавать не нужно: его добавляет Booking Service.

## Кеш — пункт 7

Flight Service кеширует информацию о рейсе и результаты поиска, включая поиск без даты.
Используется Cache-Aside: сначала Redis, при промахе — PostgreSQL и запись в кеш.
TTL — 300 секунд. Попадания и промахи видны в логах как `cache hit` и `cache miss`.

Работа с Redis собрана в [cache.py](services/flight/app/cache.py): `get()` читает
готовый protobuf-ответ, `put()` сохраняет его с TTL, `invalidate()` меняет поколение.
Кеш подключён к `GetFlight` и `SearchFlights` в `grpc_api.py`.

После `ReserveSeats`, `ReleaseReservation` и `UpdateFlight` меняется поколение кеша:
старые ключи больше не читаются и удаляются по TTL. Это защищает и от поздней записи
старого ответа. При ошибках Redis приложение читает PostgreSQL. Резервирование
всегда проверяет места в PostgreSQL под блокировкой.

Инвалидация вызывается в Flight `service.py` после успешного завершения транзакции.
Для поиска маршрут, дата и отсутствие даты образуют разные ключи.

## Retry и идемпотентность — пункт 8

Клиент Booking делает максимум три попытки с паузами 100 и 200 мс.
Повторяются только `UNAVAILABLE` и `DEADLINE_EXCEEDED`, timeout каждой попытки — 5 секунд.
Бизнес-ошибки не повторяются.

Повторы находятся в `_call_with_retry()` в Booking `flight_client.py`.
Объект запроса создаётся до цикла, поэтому параметры и API key одинаковы во всех попытках.

Все попытки `ReserveSeats` используют один `booking_id`. Повтор возвращает
существующую активную резервацию без списания мест. Другие параметры с тем же
идентификатором дают `ALREADY_EXISTS`. От дубликатов при конкуренции защищают
первичный ключ `booking_id` и транзакции. Повтор HTTP `POST /bookings` создаёт новое бронирование.

Проверка существующей резервации находится в Flight `reserve_seats()`.
Если два запроса с одним `booking_id` одновременно выбрали разные рейсы,
конфликт первичного ключа приводит к откату транзакции проигравшего запроса, включая списание мест.

## Redis Sentinel — пункт 9

В Compose настроены мастер `redis`, реплика `redis-replica` и три Sentinel.
Sentinel следят за мастером и при отказе назначают реплику новым мастером.
Flight Service использует Sentinel-клиент и подключается к текущему мастеру.
При смене мастера поколение кеша обновляется.

Начальные настройки — [master.conf](redis/master.conf), [replica.conf](redis/replica.conf)
и [sentinel.conf](redis/sentinel.conf). Общий [start.sh](redis/start.sh) копирует их
в Docker-тома: там Redis и Sentinel могут сохранять изменения ролей.
В Flight `cache.py` используется `Sentinel.master_for()`, а не фиксированный адрес мастера.

Узнать текущий мастер:

```bash
docker compose exec sentinel-1 redis-cli -p 26379 SENTINEL get-master-addr-by-name flight-cache
```

Адрес `172.30.240.10` соответствует `redis`, `172.30.240.11` — `redis-replica`.
Для проверки остановить его: `docker compose stop ИМЯ_СЕРВИСА`, дождаться смены
адреса и проверить REST-поиск. Затем вернуть узел: `docker compose start ИМЯ_СЕРВИСА`.
Реальное переключение проверено, REST-поиск продолжал работать.

## Circuit Breaker — пункт 10

Общий middleware клиента Booking после пяти последовательных сбоев за 120 секунд
переводит `CLOSED` в `OPEN`: запросы сразу получают HTTP `503` без вызова Flight Service.
Через 10 секунд разрешается одна пробная операция (`HALF_OPEN`). Если сервис доступен,
включается `CLOSED`, иначе снова `OPEN`. Бизнес-ошибки не считаются недоступностью,
операция с retry считается одним сбоем. Переходы состояний видны в логах.

Логика состояний — класс `CircuitBreaker` в
[circuit_breaker.py](services/booking/app/circuit_breaker.py).
Он оборачивает вызов с retry через `_call()` в `flight_client.py`.
Блокировка защищает состояние при параллельных запросах; во время сетевого вызова
она не удерживается. Открытый предохранитель создаёт `CircuitOpen`, который REST
обработчик переводит в `503`. Состояние хранится в памяти процесса Booking.

## Настройки и проверки

Для обычного запуска `.env` не нужен: в Compose заданы учебные значения по умолчанию.
Свои настройки можно указать, скопировав `.env.example` в `.env`.
Основные параметры: `GRPC_API_KEY`, пароли двух БД, порты приложений и параметры Circuit Breaker:

```dotenv
CB_FAILURE_THRESHOLD=5
CB_RESET_TIMEOUT=10
CB_WINDOW_SECONDS=120
```

Порог — число сбоев, таймаут — время до пробного вызова, окно — период учёта ошибок.
Время задаётся в секундах. После изменения `.env` настройки применяются командой
`docker compose up -d`.

Логи: `docker compose logs booking flight`. Статус контейнеров: `docker compose ps`.
Тесты запускаются после старта проекта. Команды для PowerShell:

```powershell
Get-Content -Raw tests/test_transactions.py | docker compose exec -T flight python - flight
Get-Content -Raw tests/test_transactions.py | docker compose exec -T booking python - booking
Get-Content -Raw tests/test_grpc_auth.py | docker compose exec -T booking python -
Get-Content -Raw tests/test_cache.py | docker compose exec -T flight python -
Get-Content -Raw tests/test_retry.py | docker compose exec -T booking python -
Get-Content -Raw tests/test_circuit_breaker.py | docker compose exec -T booking python -
```

Проверки БД создают отдельные временные схемы и удаляют их после выполнения.
Данные приложения не изменяются. Помимо тестов вручную проверены переключение
Sentinel и открытие/восстановление Circuit Breaker при остановке Flight Service.

`test_transactions.py` проверяет откаты, последнее место и конкурентные повторы;
`test_grpc_auth.py` — ключ для всех методов; `test_cache.py` — TTL, инвалидацию и
`UpdateFlight`. Повторы и переходы предохранителя проверяются в `test_retry.py`
и `test_circuit_breaker.py` с подменой сетевых ответов и времени ожидания.
