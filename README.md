# Pharmacy Notify Bot

Telegram-бот для мониторинга наличия аптечных товаров. Текущая реализация покрывает
[story #12](https://github.com/olegnysss/pharmacy_notify_bot/issues/12) и
[story #13](https://github.com/olegnysss/pharmacy_notify_bot/issues/13), а также UX-контракт
[story #14](https://github.com/olegnysss/pharmacy_notify_bot/issues/14): первый запуск,
обязательные документы, явное согласие, главное меню, команды, безопасную навигацию и
версионированный мастер выбора точного товара.

Команды личного чата:

- `/start` — onboarding или главное меню;
- `/add` — поиск или выбор товара по ссылке с пагинацией и явным подтверждением;
- `/subscriptions` — точка входа в будущий список подписок;
- `/location` и `/settings` — точки входа в будущие настройки;
- `/help` и `/privacy` — общая справка и документы;
- `/cancel` — безопасная отмена текущего сценария.

Выбор товара хранится как серверный черновик и ещё не создаёт подписку. Реальный каталог,
аптечные адаптеры, безопасная загрузка разрешённых URL и настройка области мониторинга
подключаются в следующих эпиках и stories. Пока адаптеры не подключены, production-заглушка
возвращает понятную временную ошибку и не выполняет сетевой запрос к пользовательскому URL.
Для локальной UX-проверки можно явно установить `PRODUCT_DISCOVERY_MODE=demo`: детерминированный
демо-каталог не обращается к сети и не должен использоваться как источник аптечных данных.

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
