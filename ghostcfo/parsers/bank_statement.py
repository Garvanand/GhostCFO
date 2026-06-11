"""
GhostCFO Bank Statement PDF Parser -- Multi-bank orchestrator.

Strategy (in order of reliability):
  1. pdfplumber: Structured table extraction
  2. PyMuPDF (fitz): Raw text + regex patterns
  3. Claude Vision: Scanned PDF images
  4. Manual entry prompt: Last resort

Supports: HDFC, SBI, ICICI, Axis, Kotak, and generic fallback.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Optional

import pdfplumber
from loguru import logger
from pydantic import BaseModel

from ghostcfo.models.transaction import RawTransaction


class BankType(str, Enum):
    HDFC = "hdfc"
    SBI = "sbi"
    ICICI = "icici"
    AXIS = "axis"
    KOTAK = "kotak"
    UNKNOWN = "unknown"


class ParseError(BaseModel):
    which_step_failed: str
    raw_error: str
    user_friendly_message: str
    recovery_suggestion: str


class ParseResult(BaseModel):
    transactions: list[RawTransaction]
    bank_detected: BankType = BankType.UNKNOWN
    pages_parsed: int = 0
    parser_used: str = "pdfplumber"
    new_count: int = 0
    duplicate_count: int = 0
    errors: list[ParseError] = []


# ================================================================
# BANK DETECTION
# ================================================================

_BANK_PATTERNS: list[tuple[re.Pattern, BankType]] = [
    (re.compile(r"HDFC\s*BANK", re.IGNORECASE), BankType.HDFC),
    (re.compile(r"STATE\s*BANK\s*OF\s*INDIA|SBI", re.IGNORECASE), BankType.SBI),
    (re.compile(r"ICICI\s*BANK", re.IGNORECASE), BankType.ICICI),
    (re.compile(r"AXIS\s*BANK", re.IGNORECASE), BankType.AXIS),
    (re.compile(r"KOTAK\s*MAHINDRA", re.IGNORECASE), BankType.KOTAK),
]


def detect_bank(pdf_path: str) -> BankType:
    """Detect bank from PDF header text on first 2 pages."""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages[:2]:
                text = page.extract_text() or ""
                for pattern, bank in _BANK_PATTERNS:
                    if pattern.search(text):
                        logger.info("Detected bank: {} from PDF header", bank.value)
                        return bank
    except Exception as exc:
        logger.warning("Bank detection failed: {}", exc)
    return BankType.UNKNOWN


# ================================================================
# DATE PARSING (handles multiple Indian bank date formats)
# ================================================================

_DATE_FORMATS = [
    "%d/%m/%Y",     # 01/06/2025
    "%d-%m-%Y",     # 01-06-2025
    "%d/%m/%y",     # 01/06/25
    "%d-%m-%y",     # 01-06-25
    "%d %b %Y",     # 01 Jun 2025
    "%d-%b-%Y",     # 01-Jun-2025
    "%d %b %y",     # 01 Jun 25
    "%d-%b-%y",     # 01-Jun-25
    "%Y-%m-%d",     # 2025-06-01 (ISO)
]


def parse_date(text: str) -> Optional[date]:
    """Try multiple date formats common in Indian bank statements."""
    text = text.strip().replace("  ", " ")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(text: str) -> Optional[Decimal]:
    """Parse Indian-format amounts: 1,23,456.78 or 123456.78"""
    if not text:
        return None
    cleaned = text.strip().replace(",", "").replace(" ", "")
    # Remove trailing Cr/Dr markers
    cleaned = re.sub(r"(?:Cr|Dr|CR|DR)\.?$", "", cleaned).strip()
    try:
        val = Decimal(cleaned)
        return abs(val)
    except (InvalidOperation, ValueError):
        return None


# ================================================================
# DEDUPLICATION
# ================================================================


def compute_dedup_key(txn_date: date, amount: Decimal, description: str) -> str:
    """Generate dedup key: hash(date + amount + description[:20])."""
    raw = f"{txn_date.isoformat()}|{float(amount):.2f}|{description[:20].lower()}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


def deduplicate_transactions(
    new_txns: list[RawTransaction],
    existing_keys: set[str],
) -> tuple[list[RawTransaction], int]:
    """
    Remove transactions already ingested.
    Returns (new_unique_txns, duplicate_count).
    """
    unique = []
    dup_count = 0
    for txn in new_txns:
        amount = txn.credit_amount or txn.debit_amount or Decimal("0")
        key = compute_dedup_key(txn.date, amount, txn.description)
        if key in existing_keys:
            dup_count += 1
        else:
            existing_keys.add(key)
            unique.append(txn)
    return unique, dup_count


# ================================================================
# PRIMARY PARSER: pdfplumber table extraction
# ================================================================


def parse_with_pdfplumber(pdf_path: str, bank: BankType) -> list[RawTransaction]:
    """
    Extract transactions from PDF tables using pdfplumber.

    Bank-specific column mapping handles different header layouts.
    """
    transactions: list[RawTransaction] = []

    # Column name patterns for common headers
    col_patterns = {
        "date": re.compile(r"date|txn\s*date|transaction\s*date|value\s*date", re.I),
        "description": re.compile(r"description|narration|particulars|details|remarks", re.I),
        "debit": re.compile(r"debit|withdrawal|dr|amount\s*debited", re.I),
        "credit": re.compile(r"credit|deposit|cr|amount\s*credited", re.I),
        "balance": re.compile(r"balance|closing\s*balance|running\s*bal", re.I),
        "reference": re.compile(r"ref|reference|chq|cheque|txn\s*id", re.I),
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                if not tables:
                    continue

                for table in tables:
                    if not table or len(table) < 2:
                        continue

                    # Find header row
                    header_row = None
                    header_idx = 0
                    for i, row in enumerate(table[:3]):
                        row_text = " ".join(str(c or "") for c in row).lower()
                        if col_patterns["date"].search(row_text):
                            header_row = row
                            header_idx = i
                            break

                    if not header_row:
                        continue

                    # Map columns
                    col_map: dict[str, int] = {}
                    for j, cell in enumerate(header_row):
                        cell_text = str(cell or "").strip()
                        for col_name, pattern in col_patterns.items():
                            if pattern.search(cell_text) and col_name not in col_map:
                                col_map[col_name] = j

                    if "date" not in col_map:
                        continue

                    # Parse data rows
                    for row in table[header_idx + 1:]:
                        if not row or all(not c for c in row):
                            continue

                        raw_date = str(row[col_map["date"]] or "").strip()
                        txn_date = parse_date(raw_date)
                        if not txn_date:
                            continue

                        desc = str(row[col_map.get("description", 1)] or "").strip() if "description" in col_map else ""
                        debit = parse_amount(str(row[col_map.get("debit", -1)] or "")) if "debit" in col_map else None
                        credit = parse_amount(str(row[col_map.get("credit", -1)] or "")) if "credit" in col_map else None
                        balance = parse_amount(str(row[col_map.get("balance", -1)] or "")) if "balance" in col_map else None
                        ref = str(row[col_map.get("reference", -1)] or "").strip() if "reference" in col_map else None

                        if not debit and not credit:
                            continue  # Skip rows with no amounts

                        raw_row = " | ".join(str(c or "") for c in row)

                        transactions.append(RawTransaction(
                            date=txn_date,
                            description=desc,
                            debit_amount=debit,
                            credit_amount=credit,
                            balance=balance,
                            reference_number=ref,
                            raw_row_text=raw_row,
                            parse_confidence=0.9,
                            parser_used="pdfplumber",
                        ))

    except Exception as exc:
        logger.error("pdfplumber parsing failed: {}", exc)
        raise

    logger.info("pdfplumber extracted {} transactions", len(transactions))
    return transactions


# ================================================================
# FALLBACK 1: PyMuPDF text extraction + regex
# ================================================================

def parse_with_pymupdf(pdf_path: str) -> list[RawTransaction]:
    """Fallback: extract raw text and parse with regex patterns."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF not installed, skipping fallback")
        return []

    transactions: list[RawTransaction] = []
    # Common line pattern: DATE DESCRIPTION AMOUNT AMOUNT BALANCE
    line_pattern = re.compile(
        r"(\d{2}[/-]\d{2}[/-]\d{2,4})\s+"   # date
        r"(.+?)\s+"                           # description
        r"([\d,]+\.\d{2})?\s*"                # debit (optional)
        r"([\d,]+\.\d{2})?\s*"                # credit (optional)
        r"([\d,]+\.\d{2})?",                  # balance (optional)
    )

    try:
        doc = fitz.open(pdf_path)
        for page in doc:
            text = page.get_text("text")
            for line in text.split("\n"):
                line = line.strip()
                match = line_pattern.match(line)
                if match:
                    txn_date = parse_date(match.group(1))
                    if not txn_date:
                        continue
                    desc = match.group(2).strip()
                    debit = parse_amount(match.group(3)) if match.group(3) else None
                    credit = parse_amount(match.group(4)) if match.group(4) else None
                    balance = parse_amount(match.group(5)) if match.group(5) else None

                    if not debit and not credit:
                        continue

                    transactions.append(RawTransaction(
                        date=txn_date, description=desc,
                        debit_amount=debit, credit_amount=credit, balance=balance,
                        raw_row_text=line, parse_confidence=0.7,
                        parser_used="pymupdf",
                    ))
        doc.close()
    except Exception as exc:
        logger.error("PyMuPDF parsing failed: {}", exc)

    logger.info("PyMuPDF extracted {} transactions", len(transactions))
    return transactions


# ================================================================
# FALLBACK 2: Claude Vision (scanned PDFs)
# ================================================================

async def parse_with_claude_vision(pdf_path: str) -> list[RawTransaction]:
    """Use Claude Vision API for scanned/image PDFs. Expensive."""
    try:
        import base64
        import fitz
        from ghostcfo.llm.client import get_llm_client

        doc = fitz.open(pdf_path)
        transactions: list[RawTransaction] = []
        llm = get_llm_client()

        for page_num in range(min(len(doc), 5)):  # Max 5 pages to limit cost
            page = doc[page_num]
            pix = page.get_pixmap(dpi=200)
            img_bytes = pix.tobytes("png")
            b64_img = base64.b64encode(img_bytes).decode()

            # Use Anthropic directly for vision
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=get_llm_client()._settings.anthropic_api_key)

            response = await client.messages.create(
                model="claude-opus-4-6-20250219",
                max_tokens=4096,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_img}},
                        {"type": "text", "text": (
                            "Extract ALL financial transactions from this bank statement page. "
                            "Return a JSON array with objects: {date, description, debit_amount, "
                            "credit_amount, balance}. Dates in YYYY-MM-DD. Amounts as numbers. "
                            "Return ONLY the JSON array."
                        )},
                    ],
                }],
            )

            import json
            content = response.content[0].text.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            page_txns = json.loads(content)
            for t in page_txns:
                txn_date = parse_date(t.get("date", ""))
                if not txn_date:
                    continue
                transactions.append(RawTransaction(
                    date=txn_date,
                    description=t.get("description", ""),
                    debit_amount=Decimal(str(t["debit_amount"])) if t.get("debit_amount") else None,
                    credit_amount=Decimal(str(t["credit_amount"])) if t.get("credit_amount") else None,
                    balance=Decimal(str(t["balance"])) if t.get("balance") else None,
                    raw_row_text=str(t),
                    parse_confidence=0.75,
                    parser_used="claude_vision",
                ))

        doc.close()
        logger.info("Claude Vision extracted {} transactions", len(transactions))
        return transactions

    except Exception as exc:
        logger.error("Claude Vision parsing failed: {}", exc)
        return []


# ================================================================
# PASSWORD-PROTECTED PDFs
# ================================================================

def try_decrypt_pdf(pdf_path: str, passwords: Optional[list[str]] = None) -> Optional[str]:
    """
    Attempt to decrypt a password-protected PDF.
    Returns path to decrypted PDF or None.
    """
    try:
        import pikepdf
    except ImportError:
        logger.warning("pikepdf not installed")
        return None

    default_passwords = passwords or [
        "", "1234", "0000", "password",
    ]

    for pwd in default_passwords:
        try:
            with pikepdf.open(pdf_path, password=pwd) as pdf:
                decrypted_path = pdf_path.replace(".pdf", "_decrypted.pdf")
                pdf.save(decrypted_path)
                logger.info("PDF decrypted with password attempt")
                return decrypted_path
        except pikepdf.PasswordError:
            continue
        except Exception as exc:
            logger.debug("pikepdf error: {}", exc)
            continue

    return None


# ================================================================
# MAIN ORCHESTRATOR
# ================================================================


async def parse_bank_statement(
    pdf_path: str,
    existing_dedup_keys: Optional[set[str]] = None,
    user_passwords: Optional[list[str]] = None,
) -> ParseResult:
    """
    Main entry point. Tries parsers in order until one succeeds.

    1. Detect bank from headers
    2. Try pdfplumber (structured tables)
    3. Fallback to PyMuPDF (raw text + regex)
    4. Fallback to Claude Vision (scanned/image PDFs)
    5. Deduplicate against existing transactions
    """
    errors: list[ParseError] = []
    existing_keys = existing_dedup_keys or set()

    # Handle password-protected PDFs
    try:
        with pdfplumber.open(pdf_path) as _:
            pass  # Test if we can open it
    except Exception:
        decrypted = try_decrypt_pdf(pdf_path, user_passwords)
        if decrypted:
            pdf_path = decrypted
        else:
            return ParseResult(
                transactions=[], bank_detected=BankType.UNKNOWN,
                errors=[ParseError(
                    which_step_failed="pdf_open",
                    raw_error="Password-protected PDF",
                    user_friendly_message="Yeh PDF password-protected hai. Kripya password share karein.",
                    recovery_suggestion="Send the PDF password via WhatsApp",
                )],
            )

    # Detect bank
    bank = detect_bank(pdf_path)
    transactions: list[RawTransaction] = []
    parser_used = "none"

    # Try pdfplumber
    try:
        transactions = parse_with_pdfplumber(pdf_path, bank)
        parser_used = "pdfplumber"
    except Exception as exc:
        errors.append(ParseError(
            which_step_failed="pdfplumber",
            raw_error=str(exc),
            user_friendly_message="PDF parsing mein thodi dikkat aayi, dusra method try kar rahe hain.",
            recovery_suggestion="",
        ))

    # Fallback: PyMuPDF
    if not transactions:
        try:
            transactions = parse_with_pymupdf(pdf_path)
            parser_used = "pymupdf"
        except Exception as exc:
            errors.append(ParseError(
                which_step_failed="pymupdf",
                raw_error=str(exc),
                user_friendly_message="",
                recovery_suggestion="",
            ))

    # Fallback: Claude Vision
    if not transactions:
        transactions = await parse_with_claude_vision(pdf_path)
        parser_used = "claude_vision"

    if not transactions:
        errors.append(ParseError(
            which_step_failed="all_parsers",
            raw_error="No transactions extracted",
            user_friendly_message="Hum is PDF se transactions nahi nikaal paaye. Kya aap ek cleaner copy bhej sakte hain?",
            recovery_suggestion="Upload a clearer PDF or try a different bank statement format",
        ))
        return ParseResult(transactions=[], bank_detected=bank, errors=errors)

    # Deduplicate
    unique_txns, dup_count = deduplicate_transactions(transactions, existing_keys)

    page_count = 0
    try:
        with pdfplumber.open(pdf_path) as pdf:
            page_count = len(pdf.pages)
    except Exception:
        pass

    logger.info(
        "Parsed {} | bank={} | parser={} | txns={} (new={}, dup={})",
        os.path.basename(pdf_path), bank.value, parser_used,
        len(transactions), len(unique_txns), dup_count,
    )

    return ParseResult(
        transactions=unique_txns,
        bank_detected=bank,
        pages_parsed=page_count,
        parser_used=parser_used,
        new_count=len(unique_txns),
        duplicate_count=dup_count,
        errors=errors,
    )
