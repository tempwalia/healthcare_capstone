# Intelligent Care Coordination & Referral Management Platform

An AI-agentic referral management platform (FastAPI + LangChain/LangGraph + MCP) built for the
Architect Academy capstone problem statement (`problem_statemnet.txt`).

- **`CAPSTONE_IMPLEMENTATION_GUIDE.md`** — phased build guide for the actual solution (start here).
- **`REBUILD_GUIDE.md`** — FastAPI/JWT/SQLAlchemy fundamentals reference the capstone build extends.

## Quickstart

```bash
uv sync
cp .env.example .env   # fill in DATABASE_URL / SECRET_KEY; LLMGW_API_KEY optional
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
```

On Windows, add `--loop app.core.event_loop:selector_event_loop_factory` — the
LangGraph Postgres checkpointer's driver (psycopg3, async mode) can't run
under Windows' default ProactorEventLoop, and uvicorn's built-in loop
hardcodes Proactor on win32 regardless of `asyncio`'s event-loop policy.
Not needed on Linux/macOS (including the Docker deployment).

`/docs` for Swagger UI, `/mcp` for the MCP tool interface.

## Tests

```bash
uv run pytest
```
