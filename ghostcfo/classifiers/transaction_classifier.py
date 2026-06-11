"""
GhostCFO Transaction Classifier -- 3-tier classification engine.

Tier 1: Rule-based (80+ regex patterns) -- instant, free, ~60%
Tier 2: Groq batch LLM (20 txns/call) -- fast, cheap
Tier 3: Claude for ambiguous cases -- expensive, high accuracy

Also includes income stream detection via interval analysis.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Optional

from loguru import logger

from ghostcfo.classifiers.rules import classify_by_rules
from ghostcfo.llm.client import get_llm_client
from ghostcfo.models.transaction import (
    ClassificationStats,
    IncomeStream,
    RawTransaction,
    Transaction,
    TransactionCategory,
)

# ================================================================
# LLM CLASSIFICATION PROMPT
# ================================================================

CLASSIFIER_SYSTEM_PROMPT = """You are GhostCFO's transaction classifier. You analyze Indian bank transaction descriptions and classify them.

For each transaction, return:
- category: one of [client_payment, advance, refund, interest, misc_income, saas_tools, cloud_infra, marketing, travel, food, professional_services, taxes_gst, taxes_tds, salary_contractor, equipment, office, banking_charges, utilities, insurance, loan_repayment, cash_withdrawal, rent, misc_expense, self_transfer, unknown]
- counterparty: cleaned company/person name (e.g., "ACME CORP PRIVATE L" -> "Acme Corp")
- is_income: true if this is income, false if expense
- is_recurring: true if this looks like a recurring payment
- cleaned_description: human-readable description
- confidence: 0.0-1.0

Indian bank description patterns:
- IMPS/NEFT/RTGS + reference + party name
- UPI/ + VPA or name
- ATM/CASH for withdrawals
- POS + merchant for card payments
- ECS/NACH for auto-debits

Return ONLY a JSON array. One object per transaction. No explanation."""

CLASSIFIER_FEW_SHOT = [
    {"role": "user", "content": '''Classify these transactions:
1. "NEFT CR-ACME CORP PRIVATE LIMITED-REF123456" (credit, 150000.00)
2. "UPI/123456/SWIGGY/9876543210" (debit, 450.00)
3. "IMPS/789/SELF TRANSFER/ACC12345" (debit, 50000.00)'''},
    {"role": "assistant", "content": '''[
  {"category": "client_payment", "counterparty": "Acme Corp", "is_income": true, "is_recurring": true, "cleaned_description": "Payment from Acme Corp via NEFT", "confidence": 0.95},
  {"category": "food", "counterparty": "Swiggy", "is_income": false, "is_recurring": false, "cleaned_description": "Swiggy food order", "confidence": 0.98},
  {"category": "self_transfer", "counterparty": "Self", "is_income": false, "is_recurring": false, "cleaned_description": "Self transfer between accounts", "confidence": 0.92}
]'''},
]


def _clean_counterparty(raw: str) -> str:
    """Clean company names from bank descriptions."""
    if not raw:
        return ""
    # Remove common suffixes
    cleaned = re.sub(
        r"\b(?:PRIVATE|PVT|LTD|LIMITED|CORP|CORPORATION|INC|LLC|LLP|INDIA)\b\.?",
        "", raw, flags=re.IGNORECASE,
    ).strip()
    # Title case
    cleaned = " ".join(w.capitalize() for w in cleaned.split() if w)
    # Remove trailing punctuation
    cleaned = cleaned.rstrip(" .-/")
    return cleaned


async def classify_transactions_batch(
    raw_transactions: list[RawTransaction],
    user_id: str,
    existing_counterparties: Optional[dict[str, str]] = None,
) -> tuple[list[Transaction], ClassificationStats]:
    """
    Classify a batch of raw transactions through the 3-tier pipeline.

    Returns (classified_transactions, stats).
    """
    start = time.perf_counter()
    stats = ClassificationStats(total_transactions=len(raw_transactions))
    classified: list[Transaction] = []
    unmatched: list[tuple[int, RawTransaction]] = []  # (index, raw)

    counterparty_dict = existing_counterparties or {}

    # ── Tier 1: Rule-based ─────────────────────────────
    for i, raw in enumerate(raw_transactions):
        description = raw.description or raw.raw_row_text
        direction = "credit" if raw.credit_amount and raw.credit_amount > 0 else "debit"
        amount = raw.credit_amount or raw.debit_amount or Decimal("0")

        result = classify_by_rules(description, direction)

        if result:
            category, counterparty_hint, is_income = result
            counterparty = counterparty_hint or _extract_counterparty_from_desc(description)
            if counterparty:
                counterparty = _clean_counterparty(counterparty)

            txn = Transaction(
                user_id=user_id,
                date=raw.date,
                amount=abs(amount),
                direction=direction,
                description=description,
                cleaned_description=f"{category.value.replace('_', ' ').title()}",
                category=category,
                counterparty=counterparty,
                is_income=is_income or False,
                source="bank_pdf",
                raw_source_text=raw.raw_row_text,
                confidence=0.85,
            )
            classified.append(txn)
            stats.rule_matched_count += 1
        else:
            unmatched.append((i, raw))

    # ── Tier 2: LLM batch classification ────────────────
    if unmatched:
        llm_classified = await _llm_classify_batch(unmatched, user_id)
        classified.extend(llm_classified)
        stats.llm_classified_count = len(llm_classified)
        stats.classification_cost_usd = sum(0.001 for _ in llm_classified)  # Estimate

    # ── Post-processing ─────────────────────────────────
    confidences = [t.confidence for t in classified if t.confidence > 0]
    stats.avg_confidence = statistics.mean(confidences) if confidences else 0.0
    stats.low_confidence_count = sum(1 for t in classified if t.confidence < 0.7)
    stats.duration_ms = int((time.perf_counter() - start) * 1000)

    logger.info(
        "Classification | total={} rules={} llm={} low_conf={} | {}ms",
        stats.total_transactions, stats.rule_matched_count,
        stats.llm_classified_count, stats.low_confidence_count, stats.duration_ms,
    )

    return classified, stats


async def _llm_classify_batch(
    unmatched: list[tuple[int, RawTransaction]],
    user_id: str,
) -> list[Transaction]:
    """Classify unmatched transactions using LLM in batches of 20."""
    llm = get_llm_client()
    results: list[Transaction] = []

    # Process in batches of 20
    batch_size = 20
    for batch_start in range(0, len(unmatched), batch_size):
        batch = unmatched[batch_start:batch_start + batch_size]

        # Build prompt
        lines = []
        for idx, (orig_idx, raw) in enumerate(batch, 1):
            desc = raw.description or raw.raw_row_text
            direction = "credit" if raw.credit_amount and raw.credit_amount > 0 else "debit"
            amount = raw.credit_amount or raw.debit_amount or Decimal("0")
            lines.append(f'{idx}. "{desc}" ({direction}, {amount})')

        user_msg = "Classify these transactions:\n" + "\n".join(lines)

        try:
            resp = await llm.complete(
                messages=CLASSIFIER_FEW_SHOT + [{"role": "user", "content": user_msg}],
                system=CLASSIFIER_SYSTEM_PROMPT,
                task_type="transaction_classification",
                max_tokens=2048,
                temperature=0.1,
            )

            content = resp.content.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            classifications = json.loads(content)
            if not isinstance(classifications, list):
                classifications = [classifications]

            for (orig_idx, raw), cls_data in zip(batch, classifications):
                direction = "credit" if raw.credit_amount and raw.credit_amount > 0 else "debit"
                amount = raw.credit_amount or raw.debit_amount or Decimal("0")

                try:
                    category = TransactionCategory(cls_data.get("category", "unknown"))
                except ValueError:
                    category = TransactionCategory.UNKNOWN

                txn = Transaction(
                    user_id=user_id,
                    date=raw.date,
                    amount=abs(amount),
                    direction=direction,
                    description=raw.description or raw.raw_row_text,
                    cleaned_description=cls_data.get("cleaned_description", ""),
                    category=category,
                    counterparty=_clean_counterparty(cls_data.get("counterparty", "")),
                    is_income=cls_data.get("is_income", direction == "credit"),
                    is_recurring=cls_data.get("is_recurring", False),
                    source="bank_pdf",
                    raw_source_text=raw.raw_row_text,
                    confidence=cls_data.get("confidence", 0.7),
                )
                results.append(txn)

        except Exception as exc:
            logger.warning("LLM classification failed for batch: {}", exc)
            # Fallback: mark as unknown
            for orig_idx, raw in batch:
                direction = "credit" if raw.credit_amount and raw.credit_amount > 0 else "debit"
                amount = raw.credit_amount or raw.debit_amount or Decimal("0")
                txn = Transaction(
                    user_id=user_id, date=raw.date, amount=abs(amount),
                    direction=direction,
                    description=raw.description or raw.raw_row_text,
                    category=TransactionCategory.MISC_EXPENSE if direction == "debit" else TransactionCategory.MISC_INCOME,
                    is_income=direction == "credit",
                    source="bank_pdf", raw_source_text=raw.raw_row_text, confidence=0.3,
                )
                results.append(txn)

    return results


def _extract_counterparty_from_desc(description: str) -> str:
    """Extract counterparty name from bank description heuristically."""
    # NEFT/RTGS: usually has party name after bank ref
    neft_match = re.search(r"(?:NEFT|RTGS|IMPS).*?(?:CR|DR)?[-/]([A-Za-z\s]+?)(?:[-/]|$)", description)
    if neft_match:
        return neft_match.group(1).strip()

    # UPI: name after UPI/ref/
    upi_match = re.search(r"UPI/\d+/([A-Za-z\s.]+?)(?:/|@|$)", description)
    if upi_match:
        return upi_match.group(1).strip()

    return ""


# ================================================================
# INCOME STREAM DETECTION
# ================================================================


def detect_income_streams(
    transactions: list[Transaction],
    lookback_days: int = 90,
) -> list[IncomeStream]:
    """
    Detect recurring income streams from classified transactions.

    Groups income transactions by counterparty, analyzes payment intervals
    and amount consistency to determine frequency and reliability.
    """
    cutoff = date.today() - timedelta(days=lookback_days)
    income_txns = [
        t for t in transactions
        if t.is_income and t.date >= cutoff and t.counterparty
        and t.category not in (TransactionCategory.INTEREST, TransactionCategory.REFUND,
                               TransactionCategory.SELF_TRANSFER)
    ]

    # Group by counterparty
    by_counterparty: dict[str, list[Transaction]] = defaultdict(list)
    for t in income_txns:
        key = (t.counterparty or "Unknown").lower().strip()
        by_counterparty[key].append(t)

    streams: list[IncomeStream] = []

    for counterparty, txns in by_counterparty.items():
        if len(txns) < 1:
            continue

        txns.sort(key=lambda t: t.date)
        amounts = [float(t.amount) for t in txns]
        total = sum(amounts)
        avg_amount = statistics.mean(amounts)

        # Calculate intervals between payments
        if len(txns) >= 2:
            intervals = [
                (txns[i].date - txns[i - 1].date).days
                for i in range(1, len(txns))
            ]
            avg_interval = statistics.mean(intervals)
            interval_std = statistics.stdev(intervals) if len(intervals) > 1 else avg_interval

            # Determine frequency
            if avg_interval <= 10:
                frequency = "weekly"
            elif avg_interval <= 18:
                frequency = "biweekly"
            elif avg_interval <= 40:
                frequency = "monthly"
            elif avg_interval <= 100:
                frequency = "quarterly"
            else:
                frequency = "irregular"

            # Reliability: low std relative to mean = reliable
            reliability = max(0.0, min(1.0, 1.0 - (interval_std / max(avg_interval, 1))))

            # Estimate next payment
            expected_next = txns[-1].date + timedelta(days=int(avg_interval))
        else:
            frequency = "irregular"
            reliability = 0.3
            avg_interval = 0
            expected_next = None

        # Amount consistency boosts reliability
        if len(amounts) > 1:
            amount_cv = statistics.stdev(amounts) / avg_amount if avg_amount > 0 else 1
            if amount_cv < 0.1:  # Very consistent amounts
                reliability = min(1.0, reliability + 0.2)

        display_name = txns[0].counterparty or counterparty.title()

        streams.append(IncomeStream(
            counterparty=display_name,
            average_amount=Decimal(str(round(avg_amount, 2))),
            frequency=frequency,
            last_payment_date=txns[-1].date,
            expected_next_date=expected_next,
            reliability_score=round(reliability, 3),
            total_received_90d=Decimal(str(round(total, 2))),
            payment_count_90d=len(txns),
            avg_days_between_payments=round(avg_interval, 1) if avg_interval else 0,
        ))

    # Sort by total received (most valuable first)
    streams.sort(key=lambda s: s.total_received_90d, reverse=True)
    return streams
