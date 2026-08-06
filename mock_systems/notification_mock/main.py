"""Mock Notification / Communications system.

Contract: POST /notifications/send {user_id, channel, message} -> delivery
receipt. Exposed to agents as the MCP tool `send_notification` — the
mandatory "patient notification" step of the referral workflow. Stands in for
a real email/SMS provider (e.g. Twilio, SES); messages are recorded, not
actually delivered anywhere.
"""
from datetime import datetime, timezone
from typing import List, Literal

from fastapi import FastAPI
from fastapi_mcp import FastApiMCP
from pydantic import BaseModel

app = FastAPI(title="Mock Notification System", description="Stand-in for an external email/SMS/push provider.")

sent_messages: List[dict] = []


class SendNotificationRequest(BaseModel):
    user_id: int
    channel: Literal["email", "sms", "push"]
    message: str


class DeliveryReceipt(BaseModel):
    delivery_status: str
    channel: str
    sent_at: datetime


@app.post("/notifications/send", response_model=DeliveryReceipt, operation_id="send_notification")
async def send_notification(body: SendNotificationRequest):
    sent_at = datetime.now(timezone.utc)
    sent_messages.append({**body.model_dump(), "sent_at": sent_at})
    return DeliveryReceipt(delivery_status="delivered", channel=body.channel, sent_at=sent_at)


mcp = FastApiMCP(app)
mcp.mount_http()
