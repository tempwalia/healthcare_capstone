import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.agents import graph as agent_graph
from app.agents import mcp_clients
from app.api.dependencies.database import get_async_session
from app.core.config import settings
from app.core.rate_limit import limiter
from app.database.base import Base
from app.main import app
from tests.agent_fakes import fake_get_tools

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """slowapi's in-memory storage persists across requests within the same
    process — without this, tests accumulate against each other's /auth/login
    rate limit and start failing with 429 partway through the suite."""
    limiter.reset()
    yield


@pytest.fixture(autouse=True)
def force_stub_llm(monkeypatch):
    """`get_chat_model` (ADR-005) reads `settings.llm_enabled`/`llmgw_api_key`
    at call time, so forcing this off makes every agent node deterministically
    take the rule-based/template fallback path — no network access or real
    Groq key required to run the suite, even though the real local `.env`
    has both configured."""
    monkeypatch.setattr(settings, "llm_enabled", False)


@pytest_asyncio.fixture(autouse=True)
async def agent_graph_test_setup(monkeypatch):
    """Builds the referral workflow graph against a fresh in-memory SQLite
    checkpointer per test (real checkpoint serialize/restore, not a bare
    dict — mirrors this project's "SQLite for pytest, Postgres for the manual
    smoke test" split) and fakes the MCP tool-calling seam so nodes never
    make a real network call. `app/main.py`'s lifespan (which would otherwise
    do this against real Postgres) never runs under httpx's ASGITransport."""
    monkeypatch.setattr(mcp_clients, "get_tools", fake_get_tools)
    async with AsyncSqliteSaver.from_conn_string(":memory:") as checkpointer:
        monkeypatch.setattr(agent_graph, "_compiled_graph", agent_graph.build_graph(checkpointer))
        monkeypatch.setattr(agent_graph, "_checkpointer", checkpointer)
        yield


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def test_sessionmaker(test_engine):
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def test_session(test_sessionmaker):
    async with test_sessionmaker() as session:
        yield session


@pytest_asyncio.fixture(autouse=True)
async def patch_background_db_session(test_sessionmaker, monkeypatch):
    """BackgroundTasks (e.g. the referral workflow stub) run outside FastAPI's
    dependency-injection graph, so `app.dependency_overrides[get_async_session]`
    never applies to them — left alone, a background task started during a
    test would open a real connection to Postgres instead of the SQLite test
    DB. `app/services/referral_workflow.py` deliberately looks up
    `app.database.session.async_session` at call time (not import time) so
    this monkeypatch actually takes effect."""
    monkeypatch.setattr("app.database.session.async_session", test_sessionmaker)


@pytest_asyncio.fixture
async def test_client(test_session):
    async def override_get_db():
        yield test_session

    app.dependency_overrides[get_async_session] = override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def auth_headers(test_client, test_user_data):
    await test_client.post("/auth/register", json=test_user_data)
    login = await test_client.post(
        "/auth/login",
        data={"username": test_user_data["username"], "password": test_user_data["password"]},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_user_data():
    return {
        "email": "test@example.com",
        "username": "testuser",
        "password": "testpassword123",
    }


@pytest.fixture
def test_patient_data():
    return {
        "first_name": "John",
        "last_name": "Doe",
        "email": "john.doe@example.com",
        "phone": "+1234567890",
        "date_of_birth": "1990-01-01",
        "gender": "male",
        "address": "123 Main St, City, State",
        "emergency_contact_name": "Jane Doe",
        "emergency_contact_phone": "+1234567891",
    }


@pytest.fixture
def test_doctor_data():
    return {
        "first_name": "Sarah",
        "last_name": "Smith",
        "email": "dr.smith@hospital.com",
        "phone": "+1234567892",
        "specialization": "Cardiology",
        "license_number": "MD123456",
        "years_of_experience": 10,
        "bio": "Experienced cardiologist",
    }
