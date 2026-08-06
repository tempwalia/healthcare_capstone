"""In-process pub/sub fan-out for referral status events.

ADR-003 calls for Redis pub/sub so status fan-out works across multiple
processes; this capstone runs as a single process (ADR-001), so an in-process
broadcaster gets the same behavior — durable transactional outbox (the part
that actually matters for correctness) plus live push to connected clients —
without taking on a Redis dependency this environment doesn't have running.
Swapping this module's `publish`/`subscribe` for a Redis-backed version is a
localized change if this ever needs to scale beyond one process; nothing
else in the app (the outbox table, the SSE route) would need to change.
"""
import asyncio
from collections import defaultdict
from typing import AsyncIterator, Dict, List

_subscribers: Dict[int, List["asyncio.Queue[str]"]] = defaultdict(list)


def subscribe(referral_id: int) -> "asyncio.Queue[str]":
    queue: "asyncio.Queue[str]" = asyncio.Queue()
    _subscribers[referral_id].append(queue)
    return queue


def unsubscribe(referral_id: int, queue: "asyncio.Queue[str]") -> None:
    subscribers = _subscribers.get(referral_id)
    if subscribers and queue in subscribers:
        subscribers.remove(queue)
    if subscribers is not None and not subscribers:
        _subscribers.pop(referral_id, None)


def publish(referral_id: int, message: str) -> None:
    for queue in _subscribers.get(referral_id, []):
        queue.put_nowait(message)


async def stream(referral_id: int) -> AsyncIterator[str]:
    queue = subscribe(referral_id)
    try:
        while True:
            yield await queue.get()
    finally:
        unsubscribe(referral_id, queue)
