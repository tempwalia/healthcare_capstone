from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import graph as agent_graph
from app.api.dependencies.auth import get_current_active_user, require_permission
from app.api.dependencies.database import get_async_session
from app.api.routes.referral import _get_scoped_referral
from app.models.user import User
from app.schemas.referral import ResumeDecision

router = APIRouter(prefix="/referral-workflow", tags=["referral-workflow"])


@router.get("/{referral_id}/state", operation_id="get_workflow_state")
async def get_workflow_state(
    referral_id: int,
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(get_current_active_user),
):
    """Surfaces the LangGraph-derived fields (specialist_candidates,
    eligibility, diagnosis/procedure codes, appointment) that don't live as
    columns on `ReferralRequest` and so aren't part of `GET
    /referral/requests/{id}`'s response — scoped by the same visibility rule
    as the referral itself."""
    await _get_scoped_referral(db, current_user, referral_id)
    graph = agent_graph.get_compiled_graph()
    config = {"configurable": {"thread_id": f"referral-{referral_id}"}}
    snapshot = await graph.aget_state(config)
    return snapshot.values


@router.post("/{referral_id}/resume")
async def resume_workflow(
    referral_id: int,
    decision: ResumeDecision,
    current_user: User = Depends(require_permission("referral:approve")),
):
    graph = agent_graph.get_compiled_graph()
    config = {"configurable": {"thread_id": f"referral-{referral_id}"}}

    snapshot = await graph.aget_state(config)
    if not snapshot.interrupts:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This referral has no pending human-in-the-loop approval to resume",
        )

    result = await graph.ainvoke(Command(resume=decision.model_dump()), config=config)
    return {"status": result.get("status")}
