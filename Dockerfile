FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir .

USER app

FROM base AS runtime

CMD ["pharmacy-bot"]

FROM base AS test

USER root
RUN pip install --no-cache-dir ".[dev]"
COPY tests ./tests
USER app
CMD ["pytest"]
