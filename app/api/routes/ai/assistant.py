import logging

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.agents.assistant_graph import build_assistant_graph, resolve_role_for_tools
from app.api.dependencies.auth import get_current_active_user, oauth2_scheme
from app.api.dependencies.database import get_async_session
from app.models.user import User
from app.schemas.assistant import ChatRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/assistant", tags=["assistant"])

# Deterministic keyword-matched FAQ, ADR-005 in miniature — used whenever no
# LLM is configured. Shared across roles: it's canned, generic guidance with
# no tool calls and no patient-specific data, so there's no role-scoping
# concern here the way there is for the real tool-using agent.
_FAQ_RESPONSES = [
    (("status",), "Check a referral's current status with GET /referral/requests/{id} — it moves through submitted, awaiting_documents, eligibility_checking, awaiting_specialist_approval, scheduled, and completed."),
    (("document", "upload"), "Upload referral documents via POST /referral/requests/{id}/documents. A referral letter and recent imaging/labs are both required before a referral can proceed past intake."),
    (("appointment", "schedule", "book"), "Once a specialist candidate is approved via POST /referral-workflow/{id}/resume, the appointment is booked automatically and the referral moves to 'scheduled'."),
    (("prescription", "outcome", "follow", "diagnosis"), "Consult outcomes (symptoms, diagnosis, prescription) are recorded by care coordination staff via POST /referral/requests/{id}/outcome once the appointment has happened."),
]
_FAQ_DEFAULT = (
    "I'm running in offline mode right now (no LLM configured), so I can only answer a few common "
    "questions about referral status, documents, scheduling, and consult outcomes. For anything else, "
    "please check the relevant page directly."
)


def faq_fallback(message: str) -> str:
    text = message.lower()
    for keywords, answer in _FAQ_RESPONSES:
        if any(keyword in text for keyword in keywords):
            return answer
    return _FAQ_DEFAULT


@router.post("/chat")
async def chat(
    body: ChatRequest,
    current_user: User = Depends(get_current_active_user),
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_async_session),
):
    result = await db.execute(
        select(User).options(selectinload(User.roles)).where(User.id == current_user.id)
    )
    user = result.scalar_one()
    role = resolve_role_for_tools({r.name for r in user.roles})

    graph = await build_assistant_graph(role, token)
    if graph is None:
        return {"reply": faq_fallback(body.message)}

    config = {
        "configurable": {"thread_id": f"chat-{current_user.id}-{body.session_id}"},
        "run_name": "assistant-chat",
        "tags": ["assistant"],
        "metadata": {"role": role, "user_id": current_user.id},
    }
    try:
        result = await graph.ainvoke({"messages": [("user", body.message)]}, config=config)
        return {"reply": result["messages"][-1].content}
    except Exception:
        # A tool-call the model made (bad schema, a downstream route 4xx/5xx,
        # a malformed LLM response, ...) previously bubbled up as a raw,
        # unhandled 500 — "Sorry, something went wrong: Internal Server
        # Error" with no way for the user to recover except reloading. This
        # keeps the conversation alive (same graceful-degradation spirit as
        # faq_fallback above) instead of dead-ending the whole chat on one
        # bad turn; logged with the role/message for real debugging, not
        # swallowed silently.
        logger.exception(
            "assistant chat failed (user_id=%s, role=%s, message=%r)", current_user.id, role, body.message,
        )
        return {
            "reply": "Sorry, I couldn't answer that just now — could you try rephrasing, or ask again in a moment?"
        }
