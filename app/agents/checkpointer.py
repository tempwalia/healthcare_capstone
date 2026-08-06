from contextlib import asynccontextmanager
from typing import AsyncIterator

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import settings


@asynccontextmanager
async def open_checkpointer() -> AsyncIterator[AsyncPostgresSaver]:
    """Opens the Postgres-backed checkpointer for the app's lifetime (ADR-004
    — workflow state externalized to Postgres, not held in process memory, so
    a human-in-the-loop pause survives an API restart). `.setup()` creates the
    checkpoint tables if missing; idempotent, safe to call on every startup,
    intentionally outside Alembic's migration history since LangGraph owns
    this schema itself."""
    async with AsyncPostgresSaver.from_conn_string(settings.database_url_psycopg) as checkpointer:
        await checkpointer.setup()
        yield checkpointer
