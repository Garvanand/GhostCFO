"""
GhostCFO Invoice Models -- Receivables and payables tracking.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


class Invoice(BaseModel):
    """An invoice (sent or received)."""

    invoice_id: str = Field(default_factory=lambda: uuid4().hex[:16])
    user_id: str
    client_name: str
    client_email: Optional[str] = None
    amount: Decimal
    currency: str = "INR"
    invoice_date: date
    due_date: date
    status: Literal["sent", "partially_paid", "paid", "overdue", "disputed"] = "sent"
    payment_received: Decimal = Decimal("0")
    linked_transaction_id: Optional[str] = None  # When payment arrives
    source: Literal["gmail_detected", "manual", "razorpay", "instamojo"] = "manual"
    raw_email_id: Optional[str] = None
    gst_number: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @computed_field
    @property
    def days_overdue(self) -> int:
        """Days past due date. 0 if not yet due."""
        if self.status == "paid":
            return 0
        delta = date.today() - self.due_date
        return max(0, delta.days)

    @computed_field
    @property
    def balance_due(self) -> Decimal:
        """Remaining amount owed."""
        return self.amount - self.payment_received

    @computed_field
    @property
    def is_overdue(self) -> bool:
        return self.days_overdue > 0 and self.status != "paid"


class InvoiceScanResult(BaseModel):
    """Result of a Gmail invoice scan."""

    new_invoices: list[Invoice] = Field(default_factory=list)
    updated_invoices: list[Invoice] = Field(default_factory=list)
    emails_scanned: int = 0
    invoices_found: int = 0
    scan_duration_ms: int = 0
    errors: list[str] = Field(default_factory=list)
