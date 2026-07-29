# Pharmacy Notify Bot

Telegram-бот для мониторинга наличия аптечных товаров. Текущая реализация покрывает
[story #12](https://github.com/olegnysss/pharmacy_notify_bot/issues/12) и
[story #13](https://github.com/olegnysss/pharmacy_notify_bot/issues/13): первый запуск,
обязательные документы, явное согласие, главное меню, команды, помощь и безопасную навигацию.

Команды личного чата:

- `/start` — onboarding или главное меню;
- `/add` — точка входа в будущий мастер подписки;
- `/subscriptions` — точка входа в будущий список подписок;
- `/location` и `/settings` — точки входа в будущие настройки;
- `/help` и `/privacy` — общая справка и документы;
- `/cancel` — безопасная отмена текущего сценария.

Бизнес-логика создания и управления подписками остаётся в следующих stories. Текущие
экраны этих разделов явно сообщают границу и не создают данные.

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
