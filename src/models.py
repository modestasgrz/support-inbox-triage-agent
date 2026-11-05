from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Category(StrEnum):
    SUPPORT = "support"
    SALES = "sales"
    INVOICE = "invoice"
    SPAM = "spam"
    OTHER = "other"


class Email(BaseModel):
    id: str
    thread_id: str
    rfc822_message_id: str
    sender: str
    subject: str
    body: str
    received_at: datetime
    label_names: list[str] = []


class Classification(BaseModel):
    category: Category
    confidence: float = Field(ge=0, le=1)
    reason: str
    evidence_quote: str
    injection_suspected: bool
    injection_reason: str | None = None


class Draft(BaseModel):
    email_id: str
    thread_id: str
    to: str
    subject: str
    body: str


class Outcome(BaseModel):
    email_id: str
    status: Literal["ok", "failed"]
    category: Category | None
    confidence: float | None
    reason: str | None
    evidence_quote: str | None
    labels: list[str]
    marked_read: bool
    draft_id: str | None
    needs_review: bool
    suspicious: bool
    injection_reason: str | None
    draft_rejected: str | None
    truncated: bool
    error_type: str | None


class Counts(BaseModel):
    support: int = 0
    sales: int = 0
    invoice: int = 0
    spam: int = 0
    other: int = 0
    needs_review: int = 0
    suspicious: int = 0
    failed: int = 0
    drafts: int = 0
    skipped: int = 0
    total: int = 0


class RunReport(BaseModel):
    run_id: str
    started_at: datetime
    model: str
    outcomes: list[Outcome]
    counts: Counts
