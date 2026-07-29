# Project conventions

## Architecture

Use these dependency directions:

```text
presentation (aiogram) -> application -> domain
infrastructure (SQLAlchemy/config) -> application ports + domain
bootstrap -> all concrete components
```

- `domain`: enums and value objects with no framework imports.
- `application`: use cases, decisions, repository protocols, clocks.
- `infrastructure`: SQLAlchemy models and repository implementations.
- `presentation`: aiogram routers, keyboards, and user-facing text.
- `bootstrap`: configuration, database session factory, dependency wiring.

Create one `AsyncSession` per update or application transaction. Do not share it across concurrent tasks.

## Persistence

- PostgreSQL is the production source of truth.
- Use UTC-aware timestamps.
- Store Telegram IDs as 64-bit integers.
- Treat consent decisions as append-only audit facts.
- Enforce idempotency with database uniqueness constraints, not process memory.
- Change schema through Alembic migrations.

## Configuration

- Read secrets and environment-specific URLs from settings.
- Keep a checked-in `.env.example` without real secrets.
- Validate settings at startup.
- Never log bot tokens, document URLs containing secrets, or user medication interests.

## Checks

Run:

```bash
ruff check .
ruff format --check .
mypy src
pytest
```

When the host Python version is unsupported, run the same checks in the project container.

## Issue boundaries

Do not opportunistically implement sibling stories. Define a port, callback, or explicit placeholder for a future story and test the current boundary.
