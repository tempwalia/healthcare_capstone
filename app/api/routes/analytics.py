import json
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.nodes.specialist import infer_specialty
from app.api.dependencies.auth import require_permission
from app.api.dependencies.database import get_async_session
from app.core.time_utils import ensure_aware
from app.models.referral import ReferralDocument, ReferralRequest, ReferralWorkflowStatus
from app.models.user import User

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Referrals that have left the pipeline — excluded from the delay-risk sweep
# below since a stalled *active* referral is the actionable signal, not one
# that already finished.
_TERMINAL_STATUSES = {
    ReferralWorkflowStatus.COMPLETED.value,
    ReferralWorkflowStatus.CANCELLED.value,
    ReferralWorkflowStatus.ELIGIBILITY_DENIED.value,
}
_HIGH_DELAY_RISK_THRESHOLD = 0.8  # same threshold as Phase 10's sketch


async def _count_by_status(db: AsyncSession) -> dict[str, int]:
    rows = await db.execute(
        select(ReferralRequest.status, func.count())
        .where(ReferralRequest.deleted_at.is_(None))
        .group_by(ReferralRequest.status)
    )
    return {status: count for status, count in rows.all()}


async def _avg_time_to_schedule_hours(db: AsyncSession) -> float:
    """Approximation: `updated_at` isn't a per-transition timestamp, so this
    only covers referrals currently sitting in `scheduled` (their most recent
    update *is* the scheduling transition). Referrals that have since moved
    on to `completed` are excluded rather than counted with a misleading
    duration — a dedicated status-transition log would fix this properly,
    out of scope here."""
    rows = await db.execute(
        select(ReferralRequest.created_at, ReferralRequest.updated_at).where(
            ReferralRequest.deleted_at.is_(None),
            ReferralRequest.status == ReferralWorkflowStatus.SCHEDULED.value,
        )
    )
    deltas = [
        (ensure_aware(updated) - ensure_aware(created)).total_seconds() / 3600
        for created, updated in rows.all()
        if updated is not None
    ]
    return round(sum(deltas) / len(deltas), 2) if deltas else 0.0


async def _count_high_delay_risk(db: AsyncSession) -> int:
    rows = await db.execute(
        select(ReferralRequest.request_date, ReferralRequest.target_wait_days).where(
            ReferralRequest.deleted_at.is_(None),
            ~ReferralRequest.status.in_(_TERMINAL_STATUSES),
        )
    )
    today = datetime.now(timezone.utc).date()
    count = 0
    for request_date, target_wait_days in rows.all():
        target = target_wait_days or 14
        risk = (today - request_date).days / target
        if risk > _HIGH_DELAY_RISK_THRESHOLD:
            count += 1
    return count


async def _top_specialties(db: AsyncSession, limit: int = 5) -> list[dict]:
    """No `specialty` column exists on `referral_requests` — the same
    diagnosis-code/keyword heuristic `specialist_node` uses at runtime
    (Phase 6) is reapplied here per referral, using whatever diagnosis codes
    its uploaded documents extracted (Phase 7) plus its free-text `reason`."""
    referrals = (
        await db.execute(
            select(ReferralRequest.id, ReferralRequest.reason).where(ReferralRequest.deleted_at.is_(None))
        )
    ).all()
    if not referrals:
        return []

    referral_ids = [r.id for r in referrals]
    doc_rows = (
        await db.execute(
            select(ReferralDocument.referral_request_id, ReferralDocument.extracted_diagnosis_codes).where(
                ReferralDocument.referral_request_id.in_(referral_ids)
            )
        )
    ).all()
    codes_by_referral: dict[int, list[str]] = {}
    for referral_id, codes_json in doc_rows:
        if codes_json:
            codes_by_referral.setdefault(referral_id, []).extend(json.loads(codes_json))

    counts = Counter(
        infer_specialty(codes_by_referral.get(referral_id, []), reason or "")
        for referral_id, reason in referrals
    )
    return [{"specialty": specialty, "count": count} for specialty, count in counts.most_common(limit)]


async def _eligibility_denial_rate(db: AsyncSession) -> float:
    total = (
        await db.execute(
            select(func.count()).select_from(ReferralRequest).where(ReferralRequest.deleted_at.is_(None))
        )
    ).scalar_one()
    if total == 0:
        return 0.0

    denied = (
        await db.execute(
            select(func.count())
            .select_from(ReferralRequest)
            .where(
                ReferralRequest.deleted_at.is_(None),
                ReferralRequest.status == ReferralWorkflowStatus.ELIGIBILITY_DENIED.value,
            )
        )
    ).scalar_one()
    return round(denied / total, 4)


@router.get("/referrals/summary")
async def referral_summary(
    db: AsyncSession = Depends(get_async_session),
    current_user: User = Depends(require_permission("analytics:view")),
):
    return {
        "by_status": await _count_by_status(db),
        "avg_time_to_schedule_hours": await _avg_time_to_schedule_hours(db),
        "delay_risk_referrals": await _count_high_delay_risk(db),
        "top_specialties_requested": await _top_specialties(db),
        "eligibility_denial_rate": await _eligibility_denial_rate(db),
    }
