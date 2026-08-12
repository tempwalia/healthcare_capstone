import logging

from app.agents import graph as agent_graph
from app.database import session as db_session
from app.models.referral import ReferralRequest

logger = logging.getLogger(__name__)


async def run_referral_workflow(referral_id: int) -> None:
    """Runs the real LangGraph referral workflow (intake -> eligibility ->
    specialist recommendation -> human-in-the-loop approval -> scheduling ->
    notification) as a FastAPI BackgroundTask, so `POST /referral/requests/`
    never blocks on it. The graph itself pauses at `await_specialist_approval`
    (an `interrupt()`) — `ainvoke` simply returns once that happens, since
    each node has already committed its own state change to Postgres.

    Deliberately does `db_session.async_session()` (module-attribute lookup)
    rather than `from app.database.session import async_session` — background
    tasks run outside FastAPI's dependency-injection graph, so they never go
    through `app.dependency_overrides[get_async_session]`. Importing the name
    directly would bind it at import time and permanently hit real Postgres
    even in tests; looking it up via the module lets tests monkeypatch
    `app.database.session.async_session` and have it actually take effect.
    The graph accessor follows the same convention — see `app/agents/graph.py`.
    """
    async with db_session.async_session() as db:
        referral = await db.get(ReferralRequest, referral_id)
        if referral is None or referral.deleted_at is not None:
            logger.warning("referral_workflow: referral %s not found, skipping", referral_id)
            return
        patient_id = referral.patient_id

    initial_state = {
        "referral_id": referral_id,
        "patient_id": patient_id,
        "status": "intake_processing",
    }
    config = {
        "configurable": {"thread_id": f"referral-{referral_id}"},
        # run_name/tags/metadata organize LangSmith traces (LANGSMITH_TRACING
        # in .env) by which of this app's two LangGraph apps produced them —
        # inert, zero-cost no-ops when tracing is off.
        "run_name": "referral-workflow",
        "tags": ["referral-workflow"],
        "metadata": {"referral_id": referral_id},
    }
    await agent_graph.get_compiled_graph().ainvoke(initial_state, config=config)
