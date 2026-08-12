import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv

# Must run before any other project import: `pydantic-settings`' own
# `env_file=".env"` handling (app/core/config.py) only parses the file into
# its own Settings object, it never calls os.environ.update(...) — so
# LangSmith's tracer, which auto-detects tracing purely from real process
# environment variables (LANGSMITH_TRACING/LANGSMITH_API_KEY/etc.), would
# otherwise never see a local .env's values (Docker gets this for free via
# docker-compose.yml's `env_file:`, which does set real container env vars —
# this call brings local `uv run uvicorn` dev to the same behavior).
load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi_mcp import FastApiMCP
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.agents import graph as agent_graph
from app.agents.checkpointer import open_checkpointer
from app.api.routes import (
    admin,
    analytics,
    appointments,
    audit,
    auth,
    doctors,
    health,
    medical_records,
    notifications,
    patients,
    referral,
    schedule,
)
from app.api.routes.ai import assistant as ai_assistant
from app.api.routes.ai import referral_workflow as ai_referral_workflow
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.rate_limit import limiter
from app.events.publisher import publish_loop
from app.middlewares.correlation import CorrelationIdMiddleware
from app.middlewares.cors import add_cors_middleware
from app.middlewares.no_cache_dashboard import NoCacheDashboardMiddleware
from knowledge_base.main import mcp as kb_mcp_server
from mock_systems.ehr_mock.main import app as ehr_mock_app
from mock_systems.notification_mock.main import app as notification_mock_app
from mock_systems.payer_mock.main import app as payer_mock_app
from mock_systems.provider_directory_mock.main import app as provider_directory_mock_app
from mock_systems.scheduling_mock.main import app as scheduling_mock_app

configure_logging(settings.debug)

# Built here (not inside lifespan) because mounting it below requires the
# ASGI app now, and `FastMCP.session_manager` (used in lifespan) is only
# accessible *after* streamable_http_app() has run once — it lazily creates
# the session manager on first call (see knowledge_base/main.py's docstring).
kb_asgi_app = kb_mcp_server.streamable_http_app()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Never runs under httpx's ASGITransport (tests) — only a real uvicorn
    # process triggers lifespan — so this can't race against test teardown.
    # Tests instead build the graph directly against a per-test SQLite
    # checkpointer (see tests/conftest.py).
    publisher_task = asyncio.create_task(publish_loop())
    async with open_checkpointer() as checkpointer, kb_mcp_server.session_manager.run():
        # kb_mcp_server.session_manager.run() starts the task group that
        # services every /kb/mcp request (stateless or not — confirmed in
        # mcp/server/streamable_http_manager.py: handle_request() raises if
        # it hasn't been entered). Mounting kb_asgi_app below does NOT do
        # this on its own — Starlette sends exactly one lifespan event, to
        # this root app, and never propagates it into a Mount()ed sub-app's
        # own lifespan. Composed here explicitly instead.
        await agent_graph.init_graph(checkpointer)
        yield
    publisher_task.cancel()


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
    description="Intelligent Care Coordination & Referral Management Platform",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

add_cors_middleware(app)
app.add_middleware(CorrelationIdMiddleware)
app.add_middleware(NoCacheDashboardMiddleware)

app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(doctors.router)
app.include_router(appointments.router)
app.include_router(medical_records.router)
app.include_router(audit.router)
app.include_router(health.router)
app.include_router(referral.router)
app.include_router(schedule.router)
app.include_router(analytics.router)
app.include_router(admin.router)
app.include_router(notifications.router)
app.include_router(ai_referral_workflow.router)
app.include_router(ai_assistant.router)


@app.get("/")
async def root():
    return {"message": f"Welcome to {settings.app_name}"}


# Each mocked external system is its own FastAPI app with its own MCP mount —
# genuinely separate MCP servers (own tool namespace, own dataset), just
# co-hosted in this process for capstone simplicity (see ADR-001). The agent
# layer (Phase 6) talks to these over MCP, never by importing this code.
app.mount("/mock/ehr", ehr_mock_app)
app.mount("/mock/payer", payer_mock_app)
app.mount("/mock/directory", provider_directory_mock_app)
app.mount("/mock/scheduling", scheduling_mock_app)
app.mount("/mock/notification", notification_mock_app)

# The local policy knowledge base's own MCP server (Phase 14-ish addition) —
# first-party content, not a stand-in for an external org, so it's not under
# /mock/* despite the structural similarity. Exposes real MCP tools,
# resources, *and* prompt templates (see knowledge_base/main.py) — the one
# server in this project built on the raw `mcp` SDK's FastMCP instead of
# fastapi_mcp, since fastapi_mcp only ever produces tools.
app.mount("/kb", kb_asgi_app)

# The interactive role-based dashboard (plain HTML/CSS/vanilla JS, no build
# step) — served same-origin so it can call the API above with no CORS
# config needed. Mounted at /app, not /, so it can never shadow an API route
# or /docs; client-side hash routing (#/patients, #/referrals/12) means no
# server-side catch-all is required for deep links.
app.mount("/app", StaticFiles(directory="static", html=True), name="dashboard")


# Mounted last so operation_ids from every router above are finalized before
# they're exposed as MCP tool names.
mcp = FastApiMCP(app)
mcp.mount_http()
