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
from app.models.outbox import OutboxEvent
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


_SUBMITTED_EVENT = "referral.submitted"
_SCHEDULED_EVENT = "referral.appointment.scheduled"


async def _avg_time_to_schedule_hours(db: AsyncSession) -> float:
    """Was previously approximated from `ReferralRequest.updated_at`, which
    only holds the *most recent* transition — so it only covered referrals
    currently sitting in `scheduled` and silently dropped any that had since
    moved on to `completed`. Fixed by reading the actual milestone timestamps
    back from the durable outbox_events trail (app/models/outbox.py, also
    what the referral timeline endpoint reads) instead: pair each referral's
    `referral.submitted` event with its `referral.appointment.scheduled`
    event and average the deltas across every referral that ever reached
    `scheduled`, regardless of its current status."""
    rows = (
        await db.execute(
            select(OutboxEvent.referral_id, OutboxEvent.event_type, OutboxEvent.created_at)
            .join(ReferralRequest, ReferralRequest.id == OutboxEvent.referral_id)
            .where(
                ReferralRequest.deleted_at.is_(None),
                OutboxEvent.event_type.in_([_SUBMITTED_EVENT, _SCHEDULED_EVENT]),
            )
            .order_by(OutboxEvent.created_at)
        )
    ).all()

    submitted_at: dict[int, datetime] = {}
    scheduled_at: dict[int, datetime] = {}
    for referral_id, event_type, created_at in rows:
        # `.order_by(created_at)` above + dict assignment means the *first*
        # occurrence wins for each referral_id — relevant for
        # referral.submitted, which is only ever written once anyway, and
        # harmless for referral.appointment.scheduled (a referral is only
        # scheduled once in this workflow).
        target = submitted_at if event_type == _SUBMITTED_EVENT else scheduled_at
        target.setdefault(referral_id, created_at)

    deltas = [
        (ensure_aware(scheduled_at[rid]) - ensure_aware(submitted_at[rid])).total_seconds() / 3600
        for rid in scheduled_at
        if rid in submitted_at
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


@router.get("/referrals/summary", operation_id="get_referral_analytics_summary")
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
