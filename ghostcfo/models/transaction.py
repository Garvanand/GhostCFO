"""
GhostCFO Transaction Models -- Core financial data schemas.

Every financial event in GhostCFO is a Transaction. This module defines
the Transaction model, category enum, income stream detection schema,
and the raw (pre-classification) transaction model from parsers.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, computed_field


class TransactionCategory(str, Enum):
    """All categories relevant to Indian freelancer/founder finances."""

    # Income
    CLIENT_PAYMENT = "client_payment"
    ADVANCE = "advance"
    REFUND = "refund"
    INTEREST = "interest"
    MISC_INCOME = "misc_income"

    # Expense
    SAAS_TOOLS = "saas_tools"
    CLOUD_INFRA = "cloud_infra"
    MARKETING = "marketing"
    TRAVEL = "travel"
    FOOD = "food"
    PROFESSIONAL_SERVICES = "professional_services"
    TAXES_GST = "taxes_gst"
    TAXES_TDS = "taxes_tds"
    SALARY_CONTRACTOR = "salary_contractor"
    EQUIPMENT = "equipment"
    OFFICE = "office"
    BANKING_CHARGES = "banking_charges"
    UTILITIES = "utilities"
    INSURANCE = "insurance"
    LOAN_REPAYMENT = "loan_repayment"
    CASH_WITHDRAWAL = "cash_withdrawal"
    RENT = "rent"
    MISC_EXPENSE = "misc_expense"

    # Transfer (not income/expense)
    SELF_TRANSFER = "self_transfer"

    # Unclassified
    UNKNOWN = "unknown"


class RawTransaction(BaseModel):
    """Pre-classification transaction from bank statement parsers."""

    date: date
    description: str
    debit_amount: Optional[Decimal] = None
    credit_amount: Optional[Decimal] = None
    balance: Optional[Decimal] = None
    reference_number: Optional[str] = None
    raw_row_text: str = ""
    parse_confidence: float = 1.0
    parser_used: str = "unknown"


class Transaction(BaseModel):
    """Fully classified financial transaction."""

    transaction_id: str = Field(default_factory=lambda: uuid4().hex[:16])
    user_id: str
    date: date
    amount: Decimal  # Always positive
    direction: Literal["credit", "debit"]
    description: str  # Raw bank description
    cleaned_description: str = ""  # AI-cleaned version
    category: TransactionCategory = TransactionCategory.UNKNOWN
    subcategory: Optional[str] = None
    counterparty: Optional[str] = None  # Client name / vendor name
    is_income: bool = False
    is_recurring: bool = False
    recurrence_pattern: Optional[str] = None  # "monthly", "weekly"
    source: Literal["bank_pdf", "upi_csv", "gmail_invoice", "manual"] = "bank_pdf"
    raw_source_text: str = ""  # Original text before parsing
    confidence: float = 0.0  # AI classification confidence
    is_encrypted: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @computed_field
    @property
    def signed_amount(self) -> Decimal:
        """Positive for credit, negative for debit."""
        return self.amount if self.direction == "credit" else -self.amount


class IncomeStream(BaseModel):
    """A detected recurring income source."""

    stream_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    counterparty: str
    average_amount: Decimal
    frequency: Literal["weekly", "biweekly", "monthly", "quarterly", "irregular"]
    last_payment_date: date
    expected_next_date: Optional[date] = None
    reliability_score: float = 0.0  # 0-1, how consistent is this payer
    total_received_90d: Decimal = Decimal("0")
    payment_count_90d: int = 0
    avg_days_between_payments: float = 0.0


class CategorySummary(BaseModel):
    """Aggregated spending in a single category."""

    category: TransactionCategory
    total_amount: Decimal
    transaction_count: int
    percentage_of_total: float = 0.0
    avg_monthly: Decimal = Decimal("0")
    trend: Literal["increasing", "stable", "decreasing"] = "stable"


class ClassificationStats(BaseModel):
    """Statistics from a batch classification run."""

    total_transactions: int = 0
    rule_matched_count: int = 0
    llm_classified_count: int = 0
    low_confidence_count: int = 0
    avg_confidence: float = 0.0
    classification_cost_usd: float = 0.0
    duration_ms: int = 0
