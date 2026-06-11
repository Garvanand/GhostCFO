"""
GhostCFO Evaluation Harness Scaffold.

Evaluates classification accuracy and briefing quality against 
synthetic transaction histories.
"""

from __future__ import annotations

import asyncio

from loguru import logger

from ghostcfo.classifiers.transaction_classifier import classify_transactions_batch
from ghostcfo.models.transaction import RawTransaction


async def run_evals():
    """Run the evaluation suite."""
    logger.info("Starting GhostCFO Eval Suite")
    
    # Simple synthetic case
    from datetime import date
    from decimal import Decimal
    
    raw = [
        RawTransaction(
            date=date.today(),
            description="UPI/12345/SWIGGY/987",
            debit_amount=Decimal("450.00"),
            raw_row_text="UPI/12345/SWIGGY/987 450.00",
        ),
        RawTransaction(
            date=date.today(),
            description="NEFT-ACME CORP-SALARY-REF99",
            credit_amount=Decimal("150000.00"),
            raw_row_text="NEFT-ACME CORP-SALARY-REF99 150000.00",
        )
    ]
    
    classified, stats = await classify_transactions_batch(raw, "test_user")
    
    assert len(classified) == 2
    for t in classified:
        logger.info("Classified: {} -> {} ({})", t.description, t.category.value, t.counterparty)

if __name__ == "__main__":
    asyncio.run(run_evals())
