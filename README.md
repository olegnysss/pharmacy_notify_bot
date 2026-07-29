# Pharmacy Notify Bot

Telegram-бот для мониторинга наличия аптечных товаров. Текущая реализация покрывает
[story #12](https://github.com/olegnysss/pharmacy_notify_bot/issues/12) и
[story #13](https://github.com/olegnysss/pharmacy_notify_bot/issues/13), а также UX-контракт
[story #14](https://github.com/olegnysss/pharmacy_notify_bot/issues/14): первый запуск,
обязательные документы, явное согласие, главное меню, команды, безопасную навигацию и
версионированный мастер выбора точного товара. Story
[#15](https://github.com/olegnysss/pharmacy_notify_bot/issues/15) добавляет локацию, радиус,
выбор доступных источников и фильтров, режим завершения, итоговую проверку и идемпотентное
создание подписки.
Story [#16](https://github.com/olegnysss/pharmacy_notify_bot/issues/16) добавляет защищённый
список и карточку подписок, фильтры, пагинацию, честное отображение свежести и безопасный
check-gate для команды «Проверить сейчас».
Story [#17](https://github.com/olegnysss/pharmacy_notify_bot/issues/17) реализует полный
жизненный цикл подписки: атомарное редактирование с экраном изменений, паузу и возобновление
с повторной проверкой конфигурации, режимы завершения и подтверждаемое мягкое удаление с
аудитом.
Story [#18](https://github.com/olegnysss/pharmacy_notify_bot/issues/18) добавляет
пользовательские defaults локации, радиуса и сетей, часовой пояс, поддерживаемую локаль,
предпочтения уведомлений и централизованные квоты. Defaults явно попадают только в новые
черновики и никогда не переписывают существующие подписки.
Story [#19](https://github.com/olegnysss/pharmacy_notify_bot/issues/19) завершает базовый
контур надёжности Telegram-диалогов: серверные черновики имеют версию схемы и TTL, `/start`
предлагает безопасное продолжение, повторные Telegram updates дедуплицируются с lease,
устаревшие callbacks проверяются по generation и владельцу, а неизвестные ошибки получают
локализованное безопасное сообщение и correlation ID.
Story [#60](https://github.com/olegnysss/pharmacy_notify_bot/issues/60) вводит канонический
каталог: клинически значимую identity signature, Decimal-нормализацию дозировок и единиц,
устойчивые identifiers, provenance и неизменяемые версии идентичности. Предложения аптечных
источников будут подключены к этой модели следующей story.
Story [#64](https://github.com/olegnysss/pharmacy_notify_bot/issues/64) добавляет отдельные
карточки предложений источников: устойчивый ключ `source_code/external_id`, allowlist URL,
ограниченный типизированный payload, семантический fingerprint, append-only историю изменений
и стабильную пагинацию поиска с формой, дозировкой и упаковкой.
Story [#68](https://github.com/olegnysss/pharmacy_notify_bot/issues/68) добавляет объяснимый
matching: critical mismatch проверяется до scoring и trusted ID, а `probable`/`candidate`
не разрешают автоматическое событие без активного подтверждения. Подтверждения ограничены
user/source/global scope, защищены idempotency key и отзываются без удаления аудита.
Story [#72](https://github.com/olegnysss/pharmacy_notify_bot/issues/72) защищает мониторинг
от drift карточки: изменение переводит предложение в revalidation, critical/incomplete drift
карантинится, а выпуск требует exact/confirmed mapping и новой проверки. Delivery guard
сверяет версию наблюдения непосредственно перед событием, не затрагивая другие предложения.
Story [#80](https://github.com/olegnysss/pharmacy_notify_bot/issues/80) вводит типизированные
географические scope для страны, региона, города, района, адреса, радиуса, списка аптек и
online-региона. Scope имеет детерминированный fingerprint и версии, а eligibility возвращает
`eligible/ineligible/unknown`; точка без координат никогда не проходит radius-фильтр.
Story [#81](https://github.com/olegnysss/pharmacy_notify_bot/issues/81) добавляет заменяемый
geocoder port и bounded provider DTO. Результат классифицируется как exact/ambiguous/insufficient,
а подтверждение хранится без raw query и привязано к внутреннему user ID, generation и TTL;
provenance включает provider, external result и data version.
Story [#82](https://github.com/olegnysss/pharmacy_notify_bot/issues/82) вводит канонические
`Pharmacy` и версионированные `SourcePharmacy`. Дедупликация учитывает сеть, адрес, координаты
и trusted ID, сомнительные связи требуют операторского решения, а radius search исключает
точки без координат и использует стабильный cursor `(distance, pharmacy_id)`.
Story [#83](https://github.com/olegnysss/pharmacy_notify_bot/issues/83) разделяет физический
остаток, самовывоз, доставку и неизвестную online-доступность. Ссылки типа защищены domain-
инвариантами и CHECK constraint, delivery не проходит radius/pharmacy-list scope, а безопасные
presentation DTO явно говорят, когда источник не подтверждает остаток в физической аптеке.
Story [#100](https://github.com/olegnysss/pharmacy_notify_bot/issues/100) добавляет
версионируемый реестр источников: типизированные capabilities, HTTPS host/redirect allowlist,
лимиты свежести, запросов, параллелизма и кэша. Любая операция разрешается только активному
источнику с явной capability и статусом legal `allowed`; изменения конфигурации сохраняются
в безопасном аудите без credentials и требуют ожидаемую версию.
Story [#101](https://github.com/olegnysss/pharmacy_notify_bot/issues/101) вводит единый
защищённый transport: сквозной timeout budget, bounded retry с jitter и capped Retry-After,
изолированные token bucket, semaphore и circuit breaker для каждого источника. Реальный
aiohttp wire проверяет TLS, не следует редиректам автоматически и не хранит cookies; policy
проверяет каждый redirect host, content type и размер streaming-ответа до бизнес-парсинга,
а диагностика не содержит query, headers или body.
Story [#102](https://github.com/olegnysss/pharmacy_notify_bot/issues/102) определяет
версионируемый adapter contract для health, поиска, карточки, аптек и доступности. Immutable
DTO сохраняют отсутствующие значения как `unknown`, ограничивают строки и списки и связывают
результат с source/adapter/contract/schema provenance. Строгий codec отклоняет неизвестные
поля, а PostgreSQL receipt делает ingestion атомарным и идемпотентным по бизнес-запросу,
не смешивая его fingerprint с correlation/causation IDs.
Story [#103](https://github.com/olegnysss/pharmacy_notify_bot/issues/103) добавляет безопасный
push-контур: HMAC проверяется constant-time вместе с timestamp window до бизнес-парсинга,
duplicate delivery создаёт один receipt, а несовместимый аутентифицированный event
карантинится без сохранения raw body или secret. Namespaced cache разделяет source, operation,
region, user scope, schema и adapter version и не возвращает stale/corrupted payload.
Безопасные IntegrationRequest metrics дедуплицируются, имеют bounded retention и независимо
переводят каждый source между `healthy` и `degraded`.

Команды личного чата:

- `/start` — onboarding или главное меню;
- `/add` — поиск или выбор товара по ссылке с пагинацией и явным подтверждением;
- `/subscriptions` — список и управление подписками;
- `/location` — локация, радиус и сети по умолчанию;
- `/settings` — настройки профиля, уведомлений и отображение лимитов;
- `/help` и `/privacy` — общая справка и документы;
- `/cancel` — безопасная отмена текущего сценария.

Выбор товара и настройка подписки хранятся в серверных черновиках. Реальный каталог,
геокодер, аптечные адаптеры, безопасная загрузка разрешённых URL и фоновые проверки
подключаются в следующих эпиках и stories. Пока адаптеры не подключены, production-заглушка
возвращает понятную временную ошибку и не выполняет сетевой запрос к пользовательскому URL;
пустой набор источников не позволяет создать недействующее правило.
Для локальной UX-проверки можно явно установить `PRODUCT_DISCOVERY_MODE=demo`: детерминированный
демо-каталог, адреса и capability registry не обращаются к сети и не должны использоваться
как источник реальных аптечных данных.

## Требования

- Python 3.12 или новее;
- PostgreSQL 15 или новее;
- Telegram bot token от BotFather.

## Локальный запуск

Создайте окружение, установите зависимости и подготовьте конфигурацию:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
```

Укажите реальные HTTPS-ссылки на условия и политику конфиденциальности. Запустите PostgreSQL,
примените миграции и включите long polling:

```bash
docker compose up -d postgres
alembic upgrade head
pharmacy-bot
```

До публикации бота тексты и ссылки на документы должны пройти отдельное согласование.

## Проверки

```bash
ruff check .
ruff format --check .
mypy src
pytest
```

На машине без Python 3.12+ проверки можно выполнить в контейнере:

```bash
docker build --target test -t pharmacy-notify-bot-test .
docker run --rm pharmacy-notify-bot-test
```

## Архитектура

```text
presentation (aiogram) -> application -> domain
infrastructure (SQLAlchemy/PostgreSQL) -> application ports
bootstrap -> concrete dependencies
```

Application service принимает решения о маршруте onboarding независимо от Telegram и базы.
Решения о согласии являются append-only фактами и защищены уникальным ограничением по
пользователю, версиям обязательных документов и виду решения.
