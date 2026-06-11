"""
GhostCFO Classification Rules -- 80+ regex patterns for Indian bank transactions.

Tier 1 of the 3-tier classifier. Handles ~60% of transactions at zero cost.
Patterns cover: IMPS, NEFT, UPI, ATM, card payments, SaaS, telecom, food delivery,
government payments, EMIs, insurance, and common Indian vendors.
"""

from __future__ import annotations

import re
from typing import Optional

from ghostcfo.models.transaction import TransactionCategory

# Each rule: (compiled_regex, category, counterparty_hint, is_income_hint)
# is_income_hint: True=income, False=expense, None=depends on direction

RuleEntry = tuple[re.Pattern, TransactionCategory, Optional[str], Optional[bool]]

CLASSIFICATION_RULES: list[RuleEntry] = []

def _r(pattern: str, cat: TransactionCategory,
       counterparty: Optional[str] = None, is_income: Optional[bool] = None):
    CLASSIFICATION_RULES.append(
        (re.compile(pattern, re.IGNORECASE), cat, counterparty, is_income)
    )

# ================================================================
# INCOME PATTERNS
# ================================================================

# Salary credits
_r(r"\bSAL(?:ARY)?\b.*(?:CR|CREDIT)", TransactionCategory.CLIENT_PAYMENT, None, True)
_r(r"\bSALARY\b", TransactionCategory.CLIENT_PAYMENT, None, True)

# NEFT/RTGS/IMPS credits (likely client payments when credited)
_r(r"(?:NEFT|RTGS|IMPS).*CR", TransactionCategory.CLIENT_PAYMENT, None, True)

# Interest
_r(r"\bINT(?:EREST)?\s*(?:CREDIT|CR|PAID)\b", TransactionCategory.INTEREST, "Bank Interest", True)
_r(r"\bINTEREST\s+ON\s+(?:DEPOSIT|FD|RD)\b", TransactionCategory.INTEREST, "Bank Interest", True)

# Refunds
_r(r"\bREFUND\b", TransactionCategory.REFUND, None, True)
_r(r"\bCASHBACK\b", TransactionCategory.REFUND, None, True)

# ================================================================
# TAX PATTERNS
# ================================================================

_r(r"\bGST\b.*(?:PAYMENT|PAY|PMT)", TransactionCategory.TAXES_GST, "GST Payment", False)
_r(r"\bGSTIN\b", TransactionCategory.TAXES_GST, "GST", False)
_r(r"\bTDS\b", TransactionCategory.TAXES_TDS, "TDS", False)
_r(r"\b(?:INCOME\s*TAX|IT\s*DEPT|ADVANCE\s*TAX)\b", TransactionCategory.TAXES_TDS, "Income Tax", False)
_r(r"\bCHALLAN\b.*\b(?:280|281)\b", TransactionCategory.TAXES_TDS, "Tax Challan", False)

# ================================================================
# SAAS TOOLS
# ================================================================

_r(r"\bGITHUB\b", TransactionCategory.SAAS_TOOLS, "GitHub", False)
_r(r"\bNOTION\b", TransactionCategory.SAAS_TOOLS, "Notion", False)
_r(r"\bFIGMA\b", TransactionCategory.SAAS_TOOLS, "Figma", False)
_r(r"\bLINEAR\b", TransactionCategory.SAAS_TOOLS, "Linear", False)
_r(r"\bSLACK\b", TransactionCategory.SAAS_TOOLS, "Slack", False)
_r(r"\bZOOM\b", TransactionCategory.SAAS_TOOLS, "Zoom", False)
_r(r"\bCANVA\b", TransactionCategory.SAAS_TOOLS, "Canva", False)
_r(r"\bADOBE\b", TransactionCategory.SAAS_TOOLS, "Adobe", False)
_r(r"\bATLASSIAN\b", TransactionCategory.SAAS_TOOLS, "Atlassian", False)
_r(r"\bJIRA\b", TransactionCategory.SAAS_TOOLS, "Atlassian Jira", False)
_r(r"\bVERCEL\b", TransactionCategory.SAAS_TOOLS, "Vercel", False)
_r(r"\bNETLIFY\b", TransactionCategory.SAAS_TOOLS, "Netlify", False)
_r(r"\bHERETICLE|HEROKU\b", TransactionCategory.SAAS_TOOLS, "Heroku", False)
_r(r"\bOPENAI\b", TransactionCategory.SAAS_TOOLS, "OpenAI", False)
_r(r"\bANTHROPIC\b", TransactionCategory.SAAS_TOOLS, "Anthropic", False)
_r(r"\bGROQ\b", TransactionCategory.SAAS_TOOLS, "Groq", False)
_r(r"\bCHATGPT\b", TransactionCategory.SAAS_TOOLS, "OpenAI ChatGPT", False)
_r(r"\bGOOGLE\s*WORKSPACE\b", TransactionCategory.SAAS_TOOLS, "Google Workspace", False)
_r(r"\bDROPBOX\b", TransactionCategory.SAAS_TOOLS, "Dropbox", False)
_r(r"\bTRELLO\b", TransactionCategory.SAAS_TOOLS, "Trello", False)
_r(r"\bMIRO\b", TransactionCategory.SAAS_TOOLS, "Miro", False)
_r(r"\bGRAMMARL", TransactionCategory.SAAS_TOOLS, "Grammarly", False)
_r(r"\b1PASSWORD|LASTPASS|BITWARDEN\b", TransactionCategory.SAAS_TOOLS, "Password Manager", False)
_r(r"\bMAILCHIMP|SENDGRID|POSTMARK\b", TransactionCategory.MARKETING, "Email Service", False)

# ================================================================
# CLOUD INFRASTRUCTURE
# ================================================================

_r(r"\bAWS\b|AMAZON\s*WEB", TransactionCategory.CLOUD_INFRA, "AWS", False)
_r(r"\bGOOGLE\s*CLOUD|GCP\b", TransactionCategory.CLOUD_INFRA, "Google Cloud", False)
_r(r"\bAZURE|MICROSOFT\s*CLOUD\b", TransactionCategory.CLOUD_INFRA, "Azure", False)
_r(r"\bDIGITALOCEAN\b", TransactionCategory.CLOUD_INFRA, "DigitalOcean", False)
_r(r"\bRENDER\b", TransactionCategory.CLOUD_INFRA, "Render", False)
_r(r"\bRAILWAY\b", TransactionCategory.CLOUD_INFRA, "Railway", False)
_r(r"\bCLOUDFLARE\b", TransactionCategory.CLOUD_INFRA, "Cloudflare", False)
_r(r"\bHETZNER\b", TransactionCategory.CLOUD_INFRA, "Hetzner", False)
_r(r"\bLINODE\b", TransactionCategory.CLOUD_INFRA, "Linode", False)

# ================================================================
# FOOD & DELIVERY
# ================================================================

_r(r"\bSWIGGY\b", TransactionCategory.FOOD, "Swiggy", False)
_r(r"\bZOMATO\b", TransactionCategory.FOOD, "Zomato", False)
_r(r"\bDUNZO\b", TransactionCategory.FOOD, "Dunzo", False)
_r(r"\bBLINKIT|GROFERS\b", TransactionCategory.FOOD, "Blinkit", False)
_r(r"\bBIGBASKET\b", TransactionCategory.FOOD, "BigBasket", False)
_r(r"\bINSTAMART\b", TransactionCategory.FOOD, "Swiggy Instamart", False)
_r(r"\bZEPTO\b", TransactionCategory.FOOD, "Zepto", False)
_r(r"\bDOMINOS|PIZZA\s*HUT|MCDONALDS|KFC|STARBUCKS|CCD\b", TransactionCategory.FOOD, None, False)

# ================================================================
# TRAVEL & TRANSPORT
# ================================================================

_r(r"\bUBER\b", TransactionCategory.TRAVEL, "Uber", False)
_r(r"\bOLA\b", TransactionCategory.TRAVEL, "Ola", False)
_r(r"\bRAPIDO\b", TransactionCategory.TRAVEL, "Rapido", False)
_r(r"\bMAKEMYTRIP|MMT\b", TransactionCategory.TRAVEL, "MakeMyTrip", False)
_r(r"\bGOIBIBO\b", TransactionCategory.TRAVEL, "Goibibo", False)
_r(r"\bIRCTC\b", TransactionCategory.TRAVEL, "IRCTC Railways", False)
_r(r"\bCLEARTRIP\b", TransactionCategory.TRAVEL, "Cleartrip", False)
_r(r"\bINDIGO|SPICEJET|AIRINDIA|VISTARA\b", TransactionCategory.TRAVEL, None, False)
_r(r"\bFASTAG|TOLL\b", TransactionCategory.TRAVEL, "Toll/FASTag", False)
_r(r"\bPETROL|DIESEL|FUEL|HPCL|BPCL|IOCL\b", TransactionCategory.TRAVEL, "Fuel", False)

# ================================================================
# UTILITIES & TELECOM
# ================================================================

_r(r"\bAIRTEL\b", TransactionCategory.UTILITIES, "Airtel", False)
_r(r"\bJIO\b", TransactionCategory.UTILITIES, "Jio", False)
_r(r"\bBSNL\b", TransactionCategory.UTILITIES, "BSNL", False)
_r(r"\bVI\s|VODAFONE|IDEA\b", TransactionCategory.UTILITIES, "Vi", False)
_r(r"\bELECTRICITY|ELEC\s*BILL|MSEDCL|TATA\s*POWER|BESCOM\b", TransactionCategory.UTILITIES, "Electricity", False)
_r(r"\bWATER\s*BILL|WATER\s*SUPPLY\b", TransactionCategory.UTILITIES, "Water", False)
_r(r"\bPIPED\s*GAS|MAHANAGAR\s*GAS|IGL\b", TransactionCategory.UTILITIES, "Gas", False)
_r(r"\bBROADBAND|ACT\s*FIBERNET|HATHWAY\b", TransactionCategory.UTILITIES, "Internet", False)

# ================================================================
# BANKING & FINANCE
# ================================================================

_r(r"\bATM\s*(?:WDL|WITHDRAWAL|W/D|CASH)\b", TransactionCategory.CASH_WITHDRAWAL, None, False)
_r(r"\bCASH\s*WITHDRAWAL\b", TransactionCategory.CASH_WITHDRAWAL, None, False)
_r(r"\bEMI\b", TransactionCategory.LOAN_REPAYMENT, None, False)
_r(r"\bLOAN\s*(?:REPAY|EMI|INST)\b", TransactionCategory.LOAN_REPAYMENT, None, False)
_r(r"\bINSURANCE|LIC\s|HDFC\s*LIFE|ICICI\s*PRUD|SBI\s*LIFE\b", TransactionCategory.INSURANCE, None, False)
_r(r"\bCHARGES|MAINT\s*CHG|SMS\s*CHG|ANNUAL\s*FEE\b", TransactionCategory.BANKING_CHARGES, "Bank", False)
_r(r"\bMIN\s*BAL\s*CHG\b", TransactionCategory.BANKING_CHARGES, "Bank", False)

# ================================================================
# RENT & OFFICE
# ================================================================

_r(r"\bRENT\b", TransactionCategory.RENT, None, False)
_r(r"\bCOWORK|WEWORK|BHIVE|INNOV8|91SPRINGBOARD\b", TransactionCategory.OFFICE, None, False)

# ================================================================
# SELF TRANSFER
# ================================================================

_r(r"\bSELF\s*TRANSFER|SELF\s*TRF|OWN\s*ACCOUNT\b", TransactionCategory.SELF_TRANSFER, "Self", None)
_r(r"\bFD\s*(?:OPEN|BOOK|CREATION)\b", TransactionCategory.SELF_TRANSFER, "FD", None)
_r(r"\bRD\s*(?:INST|DEBIT)\b", TransactionCategory.SELF_TRANSFER, "RD", None)

# ================================================================
# EQUIPMENT & SHOPPING
# ================================================================

_r(r"\bAMAZON\b(?!.*WEB)", TransactionCategory.EQUIPMENT, "Amazon", False)
_r(r"\bFLIPKART\b", TransactionCategory.EQUIPMENT, "Flipkart", False)
_r(r"\bAPPLE\b", TransactionCategory.EQUIPMENT, "Apple", False)
_r(r"\bCROMA|RELIANCE\s*DIGITAL|VIJAY\s*SALES\b", TransactionCategory.EQUIPMENT, None, False)


def classify_by_rules(description: str, direction: str = "debit") -> Optional[tuple[TransactionCategory, Optional[str], Optional[bool]]]:
    """
    Attempt rule-based classification.

    Returns (category, counterparty_hint, is_income) or None if no rule matches.
    """
    for pattern, category, counterparty, is_income in CLASSIFICATION_RULES:
        if pattern.search(description):
            # Resolve is_income if None (depends on direction)
            if is_income is None:
                resolved_income = direction == "credit"
            else:
                resolved_income = is_income
            return (category, counterparty, resolved_income)
    return None
