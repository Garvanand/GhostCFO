"""
GhostCFO Alert Evaluation Engine.

5 MVP alert types + 3 extended:
  CASH_CLIFF, OVERDUE_INVOICE, GST_DEADLINE,
  RATE_ANOMALY, EXPENSE_SPIKE, TDS_DUE, INCOME_DROP, RUNWAY_CRITICAL

Features:
  - 72-hour dedup (don't re-send same alert)
  - Severity escalation (warning -> critical)
  - Auto-resolve when condition clears
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from loguru import logger

from ghostcfo.intelligence.cashflow import CashFlowEngine
from ghostcfo.models.alert import CFOAlert, GST_DEADLINES_MONTHLY
from ghostcfo.models.invoice import Invoice
from ghostcfo.models.snapshot import BalanceProjection, FinancialSnapshot
from ghostcfo.models.transaction import (
    CategorySummary,
    Transaction,
    TransactionCategory,
)


def evaluate_all_alerts(
    snapshot: FinancialSnapshot,
    transactions: list[Transaction],
    invoices: list[Invoice],
    projections: list[BalanceProjection],
    recent_alert_dedup_keys: Optional[set[str]] = None,
) -> list[CFOAlert]:
    """
    Evaluate all alert rules against the current financial state.

    Returns list of triggered alerts (deduped against recent history).
    """
    dedup_keys = recent_alert_dedup_keys or set()
    alerts: list[CFOAlert] = []

    # 1. CASH_CLIFF
    alert = _evaluate_cash_cliff(snapshot, projections)
    if alert and alert.dedup_key not in dedup_keys:
        alerts.append(alert)

    # 2. OVERDUE_INVOICE (one per overdue invoice)
    for inv_alert in _evaluate_overdue_invoices(snapshot.user_id, invoices):
        if inv_alert.dedup_key not in dedup_keys:
            alerts.append(inv_alert)

    # 3. GST_DEADLINE
    gst_alert = _evaluate_gst_deadline(snapshot)
    if gst_alert and gst_alert.dedup_key not in dedup_keys:
        alerts.append(gst_alert)

    # 4. RATE_ANOMALY
    rate_alert = _evaluate_rate_anomaly(snapshot.user_id, transactions)
    if rate_alert and rate_alert.dedup_key not in dedup_keys:
        alerts.append(rate_alert)

    # 5. EXPENSE_SPIKE
    for spike_alert in _evaluate_expense_spikes(snapshot):
        if spike_alert.dedup_key not in dedup_keys:
            alerts.append(spike_alert)

    # 6. RUNWAY_CRITICAL
    runway_alert = _evaluate_runway_critical(snapshot)
    if runway_alert and runway_alert.dedup_key not in dedup_keys:
        alerts.append(runway_alert)

    # Sort by severity (critical first)
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: severity_order.get(a.severity, 3))

    logger.info("Alert evaluation | user={} alerts={}", snapshot.user_id, len(alerts))
    return alerts


# ================================================================
# INDIVIDUAL ALERT EVALUATORS
# ================================================================


def _evaluate_cash_cliff(
    snapshot: FinancialSnapshot,
    projections: list[BalanceProjection],
) -> Optional[CFOAlert]:
    """CASH_CLIFF: projected balance goes negative within 21 days."""
    for proj in projections[:21]:
        if proj.expected_balance < 0:
            days_to_zero = (proj.projection_date - date.today()).days
            severity = "critical" if days_to_zero < 14 else "warning"

            alert = CFOAlert(
                user_id=snapshot.user_id,
                alert_type="CASH_CLIFF",
                severity=severity,
                title=f"Cash will run out in ~{days_to_zero} days",
                evidence=(
                    f"Current balance: Rs{snapshot.current_balance:,.0f}. "
                    f"Monthly burn: Rs{snapshot.monthly_burn_rate:,.0f}. "
                    f"Expected income: Rs{snapshot.monthly_income_rate:,.0f}. "
                    f"Projected zero date: {proj.projection_date.strftime('%d %b')}."
                ),
                recommended_action=(
                    "Follow up on pending invoices. "
                    "Consider reducing discretionary spending. "
                    f"Total receivables: Rs{snapshot.total_receivables:,.0f}."
                ),
            )
            alert.compute_dedup_key()
            return alert

    return None


def _evaluate_overdue_invoices(
    user_id: str,
    invoices: list[Invoice],
) -> list[CFOAlert]:
    """OVERDUE_INVOICE: 30/60/90 day tiers."""
    alerts = []
    for inv in invoices:
        if inv.status == "paid" or not inv.is_overdue:
            continue

        if inv.days_overdue >= 90:
            severity = "critical"
        elif inv.days_overdue >= 60:
            severity = "warning"
        elif inv.days_overdue >= 30:
            severity = "info"
        else:
            continue  # Less than 30 days, no alert

        alert = CFOAlert(
            user_id=user_id,
            alert_type="OVERDUE_INVOICE",
            severity=severity,
            title=f"{inv.client_name} owes Rs{inv.balance_due:,.0f} ({inv.days_overdue}d overdue)",
            evidence=(
                f"{inv.client_name} has an outstanding invoice of Rs{inv.amount:,.0f}. "
                f"Due date: {inv.due_date.strftime('%d %b %Y')}. "
                f"Now {inv.days_overdue} days overdue. "
                f"Received so far: Rs{inv.payment_received:,.0f}."
            ),
            recommended_action=(
                "Send a payment reminder. "
                "If >60 days, consider a call. "
                "Want me to draft a follow-up message?"
            ),
        )
        alert.compute_dedup_key()
        alerts.append(alert)

    return alerts


def _evaluate_gst_deadline(snapshot: FinancialSnapshot) -> Optional[CFOAlert]:
    """GST_DEADLINE: GSTR-1 or GSTR-3B due within 7 days."""
    today = date.today()

    for filing_type, due_day in GST_DEADLINES_MONTHLY.items():
        # Next due date
        if today.day <= due_day:
            due_date = today.replace(day=due_day)
        else:
            # Next month
            if today.month == 12:
                due_date = date(today.year + 1, 1, due_day)
            else:
                due_date = date(today.year, today.month + 1, due_day)

        days_until = (due_date - today).days

        if 0 <= days_until <= 7:
            # Determine the period this filing covers
            if due_date.month == 1:
                period_month = "December"
            else:
                period_month = date(due_date.year, due_date.month - 1, 1).strftime("%B")

            alert = CFOAlert(
                user_id=snapshot.user_id,
                alert_type="GST_DEADLINE",
                severity="warning" if days_until > 3 else "critical",
                title=f"{filing_type} for {period_month} due in {days_until} days",
                evidence=(
                    f"{filing_type} for {period_month} is due on {due_date.strftime('%d %b')} -- "
                    f"{days_until} days away. "
                    f"Estimated GST liability: Rs{snapshot.gst_liability_estimate:,.0f}."
                ),
                recommended_action=(
                    f"File {filing_type} before {due_date.strftime('%d %b')}. "
                    "Late filing attracts Rs50/day penalty. "
                    "Consult your CA if needed."
                ),
                expires_at=datetime.combine(due_date + timedelta(days=1), datetime.min.time()).replace(tzinfo=timezone.utc),
            )
            alert.compute_dedup_key()
            return alert

    return None


def _evaluate_rate_anomaly(
    user_id: str,
    transactions: list[Transaction],
) -> Optional[CFOAlert]:
    """RATE_ANOMALY: effective daily rate dropped >20% vs 90-day average."""
    anomaly = CashFlowEngine.detect_rate_anomaly(transactions)
    if not anomaly:
        return None

    alert = CFOAlert(
        user_id=user_id,
        alert_type="RATE_ANOMALY",
        severity="warning",
        title=f"Daily income rate dropped {anomaly['drop_percentage']}%",
        evidence=(
            f"Your average daily income this month is Rs{anomaly['current_daily_rate']:,.0f}, "
            f"vs your 90-day average of Rs{anomaly['avg_90d_daily_rate']:,.0f}. "
            f"That's a {anomaly['drop_percentage']}% drop."
        ),
        recommended_action=(
            "Review if any clients have paused work. "
            "Check if any invoices are pending collection. "
            "Consider reaching out to dormant clients."
        ),
    )
    alert.compute_dedup_key()
    return alert


def _evaluate_expense_spikes(snapshot: FinancialSnapshot) -> list[CFOAlert]:
    """EXPENSE_SPIKE: category spend > 2x monthly average."""
    alerts = []

    for cat_summary in snapshot.top_expense_categories:
        if cat_summary.avg_monthly <= 0:
            continue

        # Compare current total (which is 90-day total) divided by 3 vs avg
        # If any single month had a spike, we detect it through avg check
        current_monthly = cat_summary.total_amount / 3
        if current_monthly > cat_summary.avg_monthly * 2 and float(cat_summary.total_amount) > 5000:
            alert = CFOAlert(
                user_id=snapshot.user_id,
                alert_type="EXPENSE_SPIKE",
                severity="info",
                title=f"{cat_summary.category.value.replace('_', ' ').title()} spend is elevated",
                evidence=(
                    f"{cat_summary.category.value.replace('_', ' ').title()}: "
                    f"Rs{current_monthly:,.0f}/month vs Rs{cat_summary.avg_monthly:,.0f} average. "
                    f"That's {cat_summary.transaction_count} transactions totaling "
                    f"Rs{cat_summary.total_amount:,.0f} over 90 days."
                ),
                recommended_action="Review recent subscriptions and one-time purchases.",
            )
            alert.compute_dedup_key()
            alerts.append(alert)

    return alerts


def _evaluate_runway_critical(snapshot: FinancialSnapshot) -> Optional[CFOAlert]:
    """RUNWAY_CRITICAL: runway < 14 days."""
    if snapshot.runway_days < 14:
        alert = CFOAlert(
            user_id=snapshot.user_id,
            alert_type="RUNWAY_CRITICAL",
            severity="critical",
            title=f"Only {snapshot.runway_days} days of runway left",
            evidence=(
                f"Current balance: Rs{snapshot.current_balance:,.0f}. "
                f"Monthly burn: Rs{snapshot.monthly_burn_rate:,.0f}. "
                f"At this rate, funds will last approximately {snapshot.runway_days} days."
            ),
            recommended_action=(
                "Prioritize invoice collection immediately. "
                f"Total receivables: Rs{snapshot.total_receivables:,.0f}. "
                "Cut non-essential expenses. Explore bridge financing options."
            ),
        )
        alert.compute_dedup_key()
        return alert

    return None
