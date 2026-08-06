import asyncio

from sqlalchemy import select

from app.events import broadcaster
from app.events.outbox import write_outbox_event
from app.events.publisher import process_pending_events
from app.models.outbox import OutboxEvent


async def test_write_outbox_event_persists_unpublished(test_session):
    await write_outbox_event(test_session, "referral.submitted", {"referral_id": 1}, referral_id=1)
    await test_session.commit()

    row = (await test_session.execute(select(OutboxEvent))).scalar_one()
    assert row.event_type == "referral.submitted"
    assert row.referral_id == 1
    assert row.published_at is None


async def test_process_pending_events_publishes_and_marks_row(test_session):
    await write_outbox_event(test_session, "referral.status.changed", {"referral_id": 42, "to_status": "scheduled"}, referral_id=42)
    await test_session.commit()

    queue = broadcaster.subscribe(42)
    try:
        published_count = await process_pending_events()
        assert published_count == 1

        message = await asyncio.wait_for(queue.get(), timeout=1)
        assert "scheduled" in message

        row = (await test_session.execute(select(OutboxEvent))).scalar_one()
        assert row.published_at is not None
    finally:
        broadcaster.unsubscribe(42, queue)


async def test_process_pending_events_is_noop_when_nothing_pending():
    assert await process_pending_events() == 0


async def test_broadcaster_only_delivers_to_matching_referral_subscribers():
    queue_a = broadcaster.subscribe(100)
    queue_b = broadcaster.subscribe(200)
    try:
        broadcaster.publish(100, "hello-a")
        assert await asyncio.wait_for(queue_a.get(), timeout=1) == "hello-a"
        assert queue_b.empty()
    finally:
        broadcaster.unsubscribe(100, queue_a)
        broadcaster.unsubscribe(200, queue_b)
