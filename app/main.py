import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi_mcp import FastApiMCP
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.agents import graph as agent_graph
from app.agents.checkpointer import open_checkpointer
from app.api.routes import (
    appointments,
    audit,
    auth,
    doctors,
    health,
    medical_records,
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
from mock_systems.ehr_mock.main import app as ehr_mock_app
from mock_systems.notification_mock.main import app as notification_mock_app
from mock_systems.payer_mock.main import app as payer_mock_app
from mock_systems.provider_directory_mock.main import app as provider_directory_mock_app
from mock_systems.scheduling_mock.main import app as scheduling_mock_app

configure_logging(settings.debug)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Never runs under httpx's ASGITransport (tests) — only a real uvicorn
    # process triggers lifespan — so this can't race against test teardown.
    # Tests instead build the graph directly against a per-test SQLite
    # checkpointer (see tests/conftest.py).
    publisher_task = asyncio.create_task(publish_loop())
    async with open_checkpointer() as checkpointer:
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

app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(doctors.router)
app.include_router(appointments.router)
app.include_router(medical_records.router)
app.include_router(audit.router)
app.include_router(health.router)
app.include_router(referral.router)
app.include_router(schedule.router)
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


# Mounted last so operation_ids from every router above are finalized before
# they're exposed as MCP tool names.
mcp = FastApiMCP(app)
mcp.mount_http()
