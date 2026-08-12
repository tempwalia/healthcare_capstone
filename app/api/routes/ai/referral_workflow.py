from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import graph as agent_graph
from app.api.dependencies.auth import get_current_active_user, require_permission
from app.api.dependencies.database import get_async_session
from app.api.routes.referral import _get_scoped_referral
from app.models.doctor import Doctor
from app.models.provider_directory_link import ProviderDirectoryLink
from app.models.user import User
from app.schemas.referral import ProviderDirectoryLinkResponse, ResumeDecision

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
    db: AsyncSession = Depends(get_async_session),
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

    # Optional specialist-identity mapping — a plain write to a real FK
    # column, entirely outside LangGraph state, committed before the graph
    # resumes. See app/models/provider_directory_link.py for why this isn't
    # just a `doctors.id == decision.doctor_id` coincidence check. Omitting
    # platform_doctor_id (the default) leaves this whole block a no-op and
    # the referral behaves exactly as it did before this feature existed.
    referral = await _get_scoped_referral(db, current_user, referral_id)
    link = (
        await db.execute(
            select(ProviderDirectoryLink).where(
                ProviderDirectoryLink.source_system == "provider_directory_mock",
                ProviderDirectoryLink.external_doctor_id == decision.doctor_id,
            )
        )
    ).scalar_one_or_none()

    if decision.platform_doctor_id is not None:
        target_doctor = await db.get(Doctor, decision.platform_doctor_id)
        if target_doctor is None or target_doctor.deleted_at is not None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "platform_doctor_id does not match a real doctor")
        if link is None:
            link = ProviderDirectoryLink(
                source_system="provider_directory_mock", external_doctor_id=decision.doctor_id,
                doctor_id=decision.platform_doctor_id, created_by_user_id=current_user.id,
            )
            db.add(link)
        else:
            link.doctor_id = decision.platform_doctor_id
        await db.flush()

    if link is not None:
        referral.specialist_id = link.doctor_id
    await db.commit()

    result = await graph.ainvoke(
        Command(resume=decision.model_dump(exclude={"platform_doctor_id"})), config=config
    )
    return {"status": result.get("status")}


@router.get("/provider-links", response_model=List[ProviderDirectoryLinkResponse])
async def list_provider_directory_links(
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("referral:approve")),
):
    """Every existing synthetic-candidate-to-real-doctor mapping, so the
    frontend can show "already linked to Dr. X" on a candidate card without
    an N+1 lookup per candidate."""
    result = await db.execute(select(ProviderDirectoryLink))
    return result.scalars().all()
