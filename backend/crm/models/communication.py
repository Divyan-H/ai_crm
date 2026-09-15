"""Pydantic models for Communication domain (delivery receipts)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ReceiptCallback(BaseModel):
    communication_id: UUID
    campaign_id: UUID
    idempotency_key: str
    status: str   # delivered | failed
    timestamp: datetime
    failure_reason: Optional[str] = None
