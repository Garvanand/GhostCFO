"""
GhostCFO Gmail Invoice Scanner (Stub).

MVP version does not implement full OAuth flow.
Provides the interface for scanning emails for invoices.
"""

from __future__ import annotations

from loguru import logger

from ghostcfo.models.invoice import InvoiceScanResult


async def scan_gmail_for_invoices(user_id: str) -> InvoiceScanResult:
    """
    Stub for Gmail invoice scanning.
    
    In production, this would:
    1. Check for valid OAuth token in DB
    2. Query Gmail API for 'has:attachment filename:pdf (invoice OR bill)'
    3. Download attachments
    4. Run them through pdfplumber/Claude Vision
    5. Extract counterparty, amount, due date
    """
    logger.info("Gmail invoice scan stub called for user={}", user_id)
    return InvoiceScanResult(
        emails_scanned=0,
        invoices_found=0,
        errors=["Gmail OAuth not configured for MVP."]
    )
