"""
GhostCFO Snapshot Models -- Daily computed financial state.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

from ghostcfo.models.transaction import CategorySummary, IncomeStream


class BalanceProjection(BaseModel):
    """Single day in a forward balance projection."""

    projection_date: date
    min_balance: Decimal  # Pessimistic (no new income)
    expected_balance: Decimal  # Base case
    max_balance: Decimal  # Optimistic (all receivables collected)
    confidence: float = 0.5


class RunwayAnalysis(BaseModel):
    """How long can the user survive at current burn?"""

    pessimistic_days: int  # No new income
    base_days: int  # Current income rate continues
    optimistic_days: int  # Income + all receivables
    monthly_burn_rate: Decimal
    monthly_income_rate: Decimal
    current_balance: Decimal
    zero_balance_date_pessimistic: Optional[date] = None
    zero_balance_date_base: Optional[date] = None


class TaxLiabilityEstimate(BaseModel):
    """GST + TDS estimate for a quarter. NOT a filing tool."""

    quarter: int  # 1-4
    financial_year: str  # "2025-26"
    taxable_income: Decimal = Decimal("0")
    gst_liability: Decimal = Decimal("0")
    gst_rate_applied: float = 0.18  # 18% default
    tds_deducted: Decimal = Decimal("0")
    advance_tax_estimate: Decimal = Decimal("0")
    disclaimer: str = (
        "This is an estimate for awareness only. "
        "Please consult a CA for actual filing."
    )


class FinancialSnapshot(BaseModel):
    """Daily computed financial state -- the core intelligence object."""

    snapshot_id: str = Field(default_factory=lambda: uuid4().hex[:16])
    user_id: str
    snapshot_date: date
    current_balance: Decimal
    runway_days: int = 0
    monthly_burn_rate: Decimal = Decimal("0")  # 90-day rolling avg
    monthly_income_rate: Decimal = Decimal("0")  # 90-day rolling avg
    total_receivables: Decimal = Decimal("0")
    overdue_receivables: Decimal = Decimal("0")
    gst_liability_estimate: Decimal = Decimal("0")
    tds_liability_estimate: Decimal = Decimal("0")
    income_streams: list[IncomeStream] = Field(default_factory=list)
    top_expense_categories: list[CategorySummary] = Field(default_factory=list)
    health_score: int = 50  # 0-100 composite score
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Computed net worth
    @property
    def net_worth_estimate(self) -> Decimal:
        return (
            self.current_balance
            + self.total_receivables
            - self.gst_liability_estimate
            - self.tds_liability_estimate
        )

    def compute_health_score(self) -> int:
        """
        Proprietary composite health score (0-100).

        Weights:
          - Runway (40%): >90d=40, 60-90=30, 30-60=20, <30=10, <14=0
          - Income diversity (20%): >3 streams=20, 2=15, 1=10
          - Receivables health (20%): 0 overdue=20, <30d=15, <60d=10, >60d=0
          - Burn stability (20%): stable=20, increasing=10, spike=0
        """
        score = 0

        # Runway component (40 points)
        if self.runway_days > 90:
            score += 40
        elif self.runway_days > 60:
            score += 30
        elif self.runway_days > 30:
            score += 20
        elif self.runway_days > 14:
            score += 10

        # Income diversity (20 points)
        streams = len(self.income_streams)
        if streams >= 3:
            score += 20
        elif streams == 2:
            score += 15
        elif streams == 1:
            score += 10

        # Receivables health (20 points)
        if self.overdue_receivables == 0:
            score += 20
        elif self.total_receivables > 0:
            overdue_ratio = float(self.overdue_receivables / self.total_receivables)
            if overdue_ratio < 0.3:
                score += 15
            elif overdue_ratio < 0.6:
                score += 10

        # Burn stability (20 points) -- simple heuristic
        if self.monthly_burn_rate > 0 and self.monthly_income_rate > 0:
            ratio = float(self.monthly_income_rate / self.monthly_burn_rate)
            if ratio > 1.5:
                score += 20
            elif ratio > 1.0:
                score += 15
            elif ratio > 0.7:
                score += 10

        self.health_score = min(100, max(0, score))
        return self.health_score
