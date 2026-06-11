"""
GhostCFO Cash Flow Intelligence Engine.

The analytical core: builds financial snapshots, projects balances forward,
calculates runway, detects rate anomalies, and estimates tax liability.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from loguru import logger

from ghostcfo.classifiers.transaction_classifier import detect_income_streams
from ghostcfo.models.alert import CFOAlert
from ghostcfo.models.invoice import Invoice
from ghostcfo.models.snapshot import (
    BalanceProjection,
    FinancialSnapshot,
    RunwayAnalysis,
    TaxLiabilityEstimate,
)
from ghostcfo.models.transaction import (
    CategorySummary,
    IncomeStream,
    Transaction,
    TransactionCategory,
)


class CashFlowEngine:
    """Core financial intelligence engine."""

    @staticmethod
    def build_snapshot(
        user_id: str,
        transactions: list[Transaction],
        invoices: list[Invoice],
        as_of_date: Optional[date] = None,
    ) -> FinancialSnapshot:
        """
        Build a complete financial snapshot from transaction history.

        Called daily by the scheduler.
        """
        today = as_of_date or date.today()
        cutoff_90d = today - timedelta(days=90)
        cutoff_30d = today - timedelta(days=30)

        # Filter to 90-day window
        recent = [t for t in transactions if t.date >= cutoff_90d and t.date <= today]

        # Current balance: last known balance or compute from transactions
        current_balance = CashFlowEngine._estimate_balance(recent)

        # Monthly aggregates
        monthly_income = CashFlowEngine._monthly_aggregate(recent, income=True)
        monthly_expense = CashFlowEngine._monthly_aggregate(recent, income=False)

        burn_rate = Decimal(str(round(statistics.mean(monthly_expense), 2))) if monthly_expense else Decimal("0")
        income_rate = Decimal(str(round(statistics.mean(monthly_income), 2))) if monthly_income else Decimal("0")

        # Runway
        if burn_rate > 0:
            net_monthly = income_rate - burn_rate
            if net_monthly <= 0:
                runway_days = int(float(current_balance) / (float(burn_rate) / 30))
            else:
                runway_days = 365  # Positive cash flow -> long runway
        else:
            runway_days = 365

        # Receivables
        pending_invoices = [inv for inv in invoices if inv.status != "paid"]
        total_receivables = sum(inv.balance_due for inv in pending_invoices)
        overdue_receivables = sum(inv.balance_due for inv in pending_invoices if inv.is_overdue)

        # Income streams
        income_streams = detect_income_streams(recent)

        # Expense categories
        expense_categories = CashFlowEngine._expense_breakdown(recent)

        # Tax estimates
        gst_est = CashFlowEngine._estimate_gst(recent)
        tds_est = CashFlowEngine._estimate_tds(recent)

        snapshot = FinancialSnapshot(
            user_id=user_id,
            snapshot_date=today,
            current_balance=current_balance,
            runway_days=runway_days,
            monthly_burn_rate=burn_rate,
            monthly_income_rate=income_rate,
            total_receivables=total_receivables,
            overdue_receivables=overdue_receivables,
            gst_liability_estimate=gst_est,
            tds_liability_estimate=tds_est,
            income_streams=income_streams,
            top_expense_categories=expense_categories[:5],
        )
        snapshot.compute_health_score()

        logger.info(
            "Snapshot built | user={} balance={} runway={}d health={} streams={}",
            user_id, current_balance, runway_days, snapshot.health_score, len(income_streams),
        )

        return snapshot

    @staticmethod
    def project_balance(
        snapshot: FinancialSnapshot,
        transactions: list[Transaction],
        invoices: list[Invoice],
        days_forward: int = 30,
    ) -> list[BalanceProjection]:
        """
        Project daily balance for next N days.

        3 scenarios:
          Pessimistic: only recurring expenses, no new income
          Base: current income + expense rate
          Optimistic: income + all receivables collected
        """
        today = date.today()
        daily_burn = float(snapshot.monthly_burn_rate) / 30
        daily_income = float(snapshot.monthly_income_rate) / 30
        balance = float(snapshot.current_balance)

        # Probability-weight receivables by overdue days
        expected_receivable_income = Decimal("0")
        for inv in invoices:
            if inv.status != "paid":
                # Older overdue -> lower probability
                if inv.days_overdue == 0:
                    prob = 0.8
                elif inv.days_overdue < 30:
                    prob = 0.6
                elif inv.days_overdue < 60:
                    prob = 0.3
                else:
                    prob = 0.1
                expected_receivable_income += inv.balance_due * Decimal(str(prob))

        receivable_daily = float(expected_receivable_income) / max(days_forward, 1)

        projections = []
        for day in range(1, days_forward + 1):
            proj_date = today + timedelta(days=day)

            pessimistic = balance - (daily_burn * day)
            base = balance + (daily_income * day) - (daily_burn * day)
            optimistic = base + (receivable_daily * day)

            projections.append(BalanceProjection(
                projection_date=proj_date,
                min_balance=Decimal(str(round(pessimistic, 2))),
                expected_balance=Decimal(str(round(base, 2))),
                max_balance=Decimal(str(round(optimistic, 2))),
                confidence=max(0.3, 1.0 - (day * 0.02)),  # Confidence decays with time
            ))

        return projections

    @staticmethod
    def calculate_runway(snapshot: FinancialSnapshot, invoices: list[Invoice]) -> RunwayAnalysis:
        """
        How long can the user survive?

        3 scenarios: pessimistic, base, optimistic.
        """
        balance = float(snapshot.current_balance)
        burn = float(snapshot.monthly_burn_rate)
        income = float(snapshot.monthly_income_rate)
        daily_burn = burn / 30 if burn > 0 else 0

        # Pessimistic: no new income
        pess_days = int(balance / daily_burn) if daily_burn > 0 else 365

        # Base: current rates continue
        net_daily = (income - burn) / 30
        if net_daily >= 0:
            base_days = 365
        elif daily_burn > 0:
            base_days = int(balance / abs(net_daily))
        else:
            base_days = 365

        # Optimistic: income + all receivables collected
        receivables = float(snapshot.total_receivables)
        total_available = balance + receivables
        if daily_burn > 0 and net_daily < 0:
            opt_days = int(total_available / abs(net_daily))
        else:
            opt_days = 365

        today = date.today()

        return RunwayAnalysis(
            pessimistic_days=min(pess_days, 365),
            base_days=min(base_days, 365),
            optimistic_days=min(opt_days, 365),
            monthly_burn_rate=snapshot.monthly_burn_rate,
            monthly_income_rate=snapshot.monthly_income_rate,
            current_balance=snapshot.current_balance,
            zero_balance_date_pessimistic=today + timedelta(days=pess_days) if pess_days < 365 else None,
            zero_balance_date_base=today + timedelta(days=base_days) if base_days < 365 else None,
        )

    @staticmethod
    def detect_rate_anomaly(
        transactions: list[Transaction],
        lookback_days: int = 90,
    ) -> Optional[dict]:
        """
        Detect if effective rate (income/working days) dropped >20%.

        Returns anomaly data dict or None.
        """
        today = date.today()
        cutoff_90d = today - timedelta(days=lookback_days)
        current_month_start = today.replace(day=1)

        # 90-day daily income rate
        income_90d = [
            t for t in transactions
            if t.is_income and t.date >= cutoff_90d and t.date < current_month_start
        ]
        total_90d = sum(float(t.amount) for t in income_90d)
        working_days_90d = lookback_days * 5 / 7  # Approximate
        rate_90d = total_90d / working_days_90d if working_days_90d > 0 else 0

        # Current month
        income_current = [t for t in transactions if t.is_income and t.date >= current_month_start]
        total_current = sum(float(t.amount) for t in income_current)
        days_in_month = (today - current_month_start).days + 1
        working_days_current = days_in_month * 5 / 7
        rate_current = total_current / working_days_current if working_days_current > 0 else 0

        if rate_90d > 0 and rate_current < rate_90d * 0.8:
            drop_pct = round((1 - rate_current / rate_90d) * 100, 1)
            return {
                "current_daily_rate": round(rate_current, 2),
                "avg_90d_daily_rate": round(rate_90d, 2),
                "drop_percentage": drop_pct,
            }

        return None

    @staticmethod
    def estimate_tax_liability(
        transactions: list[Transaction],
        quarter: int,
        year: int,
    ) -> TaxLiabilityEstimate:
        """
        Estimate GST + TDS for a quarter.

        NOT a tax filing tool -- an awareness tool.
        """
        # Determine quarter date range
        fy_start_year = year if quarter >= 1 else year - 1
        quarter_ranges = {
            1: (date(fy_start_year, 4, 1), date(fy_start_year, 6, 30)),
            2: (date(fy_start_year, 7, 1), date(fy_start_year, 9, 30)),
            3: (date(fy_start_year, 10, 1), date(fy_start_year, 12, 31)),
            4: (date(fy_start_year + 1, 1, 1), date(fy_start_year + 1, 3, 31)),
        }
        q_start, q_end = quarter_ranges.get(quarter, (date(year, 4, 1), date(year, 6, 30)))

        # Taxable income (client payments, not interest/refunds)
        taxable = [
            t for t in transactions
            if t.is_income and q_start <= t.date <= q_end
            and t.category in (TransactionCategory.CLIENT_PAYMENT, TransactionCategory.ADVANCE)
        ]
        taxable_income = sum(t.amount for t in taxable)

        # GST at 18% (default for professional services)
        gst_rate = Decimal("0.18")
        gst_liability = taxable_income * gst_rate

        # TDS already deducted (10% on professional income > 30K)
        tds_txns = [t for t in transactions if t.category == TransactionCategory.TAXES_TDS and q_start <= t.date <= q_end]
        tds_deducted = sum(t.amount for t in tds_txns)

        # Advance tax (simplified: 15% of taxable income for Q1, cumulative for Q2-4)
        advance_tax_rate = Decimal("0.15") if quarter == 1 else Decimal("0.30")
        advance_tax = taxable_income * advance_tax_rate - tds_deducted

        fy_str = f"{fy_start_year}-{str(fy_start_year + 1)[-2:]}"

        return TaxLiabilityEstimate(
            quarter=quarter,
            financial_year=fy_str,
            taxable_income=taxable_income,
            gst_liability=gst_liability,
            gst_rate_applied=float(gst_rate),
            tds_deducted=tds_deducted,
            advance_tax_estimate=max(Decimal("0"), advance_tax),
        )

    # ── Private helpers ─────────────────────────────────

    @staticmethod
    def _estimate_balance(transactions: list[Transaction]) -> Decimal:
        """Estimate current balance from transaction history."""
        if not transactions:
            return Decimal("0")

        # Sum all credits - debits
        total = Decimal("0")
        for t in transactions:
            total += t.signed_amount
        # Return abs value as a starting estimate
        # In production, user would provide opening balance
        return abs(total) if total > 0 else Decimal("50000")  # Default fallback

    @staticmethod
    def _monthly_aggregate(transactions: list[Transaction], income: bool) -> list[float]:
        """Calculate monthly totals for income or expenses."""
        by_month: dict[str, float] = defaultdict(float)
        for t in transactions:
            if t.is_income == income:
                key = t.date.strftime("%Y-%m")
                by_month[key] += float(t.amount)
        return list(by_month.values()) if by_month else [0.0]

    @staticmethod
    def _expense_breakdown(transactions: list[Transaction]) -> list[CategorySummary]:
        """Break down expenses by category."""
        by_cat: dict[TransactionCategory, list[float]] = defaultdict(list)
        for t in transactions:
            if not t.is_income and t.category != TransactionCategory.SELF_TRANSFER:
                by_cat[t.category].append(float(t.amount))

        total_expense = sum(sum(v) for v in by_cat.values())

        summaries = []
        for cat, amounts in by_cat.items():
            cat_total = sum(amounts)
            summaries.append(CategorySummary(
                category=cat,
                total_amount=Decimal(str(round(cat_total, 2))),
                transaction_count=len(amounts),
                percentage_of_total=round(cat_total / total_expense * 100, 1) if total_expense > 0 else 0,
                avg_monthly=Decimal(str(round(cat_total / 3, 2))),  # 90-day / 3 months
            ))

        summaries.sort(key=lambda s: s.total_amount, reverse=True)
        return summaries

    @staticmethod
    def _estimate_gst(transactions: list[Transaction]) -> Decimal:
        """Rough GST liability estimate for current month."""
        today = date.today()
        month_start = today.replace(day=1)
        taxable = sum(
            t.amount for t in transactions
            if t.is_income and t.date >= month_start
            and t.category == TransactionCategory.CLIENT_PAYMENT
        )
        return taxable * Decimal("0.18")

    @staticmethod
    def _estimate_tds(transactions: list[Transaction]) -> Decimal:
        """Estimate TDS liability (10% on professional income > 30K)."""
        today = date.today()
        month_start = today.replace(day=1)
        income = sum(
            t.amount for t in transactions
            if t.is_income and t.date >= month_start
            and t.category == TransactionCategory.CLIENT_PAYMENT
        )
        if income > Decimal("30000"):
            return income * Decimal("0.10")
        return Decimal("0")
