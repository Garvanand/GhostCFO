"""
GhostCFO Alert Models -- Proactive financial alerts.

5 MVP alert types:
  CASH_CLIFF, OVERDUE_INVOICE, GST_DEADLINE, RATE_ANOMALY, EXPENSE_SPIKE
Plus 3 extended types: TDS_DUE, INCOME_DROP, RUNWAY_CRITICAL
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class CFOAlert(BaseModel):
    """A single proactive financial alert."""

    alert_id: str = Field(default_factory=lambda: uuid4().hex[:16])
    user_id: str
    alert_type: Literal[
        "CASH_CLIFF",
        "OVERDUE_INVOICE",
        "GST_DEADLINE",
        "RATE_ANOMALY",
        "EXPENSE_SPIKE",
        "TDS_DUE",
        "INCOME_DROP",
        "RUNWAY_CRITICAL",
    ]
    severity: Literal["info", "warning", "critical"]
    title: str
    evidence: str  # Specific data that triggered this
    recommended_action: str
    triggered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_acknowledged: bool = False
    expires_at: Optional[datetime] = None

    # Deduplication
    dedup_key: str = ""  # hash of (user_id, alert_type, core_evidence)

    def compute_dedup_key(self) -> str:
        """Generate dedup key to prevent re-sending same alert within 72h."""
        import hashlib
        raw = f"{self.user_id}:{self.alert_type}:{self.title}"
        self.dedup_key = hashlib.md5(raw.encode()).hexdigest()[:12]
        return self.dedup_key


class BriefingRecord(BaseModel):
    """Record of a daily CFO briefing sent to a user."""

    briefing_id: str = Field(default_factory=lambda: uuid4().hex[:16])
    user_id: str
    briefing_text: str
    snapshot_id: str
    health_score: int
    alerts_included: int = 0
    tone: Literal["confident", "balanced", "direct", "urgent"] = "balanced"
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    delivery_channel: Literal["whatsapp_text", "whatsapp_voice", "both"] = "whatsapp_text"
    delivery_status: Literal["sent", "delivered", "read", "failed"] = "sent"


class UserProfile(BaseModel):
    """GhostCFO user profile."""

    user_id: str  # phone number
    phone_number: str
    name: str = ""
    language: str = "en"  # "en", "hi", "mr"
    timezone: str = "Asia/Kolkata"
    business_type: Literal[
        "freelancer", "consultant", "agency", "solo_founder", "other"
    ] = "freelancer"
    gst_registered: bool = False
    gstin: Optional[str] = None
    preferred_briefing_time: str = "08:00"
    voice_briefing_enabled: bool = False
    gmail_connected: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# -- GST Calendar (hardcoded for FY 2025-26) --

GST_DEADLINES_MONTHLY = {
    "GSTR-1": 11,  # 11th of next month
    "GSTR-3B": 20,  # 20th of next month
}

# Quarterly GSTR-1 dates (for turnover < 5 Cr under QRMP)
GST_QUARTERLY_DEADLINES = {
    "Q1": {"GSTR-1": "2025-07-13", "GSTR-3B": "2025-07-22"},
    "Q2": {"GSTR-1": "2025-10-13", "GSTR-3B": "2025-10-22"},
    "Q3": {"GSTR-1": "2026-01-13", "GSTR-3B": "2026-01-22"},
    "Q4": {"GSTR-1": "2026-04-13", "GSTR-3B": "2026-04-22"},
}
