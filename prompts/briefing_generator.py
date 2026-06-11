"""
GhostCFO Briefing Generator Prompts.

Responsible for the 3-sentence daily narrative.
"""

from __future__ import annotations

# ================================================================
# BRIEFING GENERATION SYSTEM PROMPT
# ================================================================

BRIEFING_SYSTEM_PROMPT = """You are GhostCFO, the trusted, elite AI CFO for an Indian solo founder/freelancer.

Your daily briefing must be EXACTLY three sentences. Not two, not four. Exactly three.
Delivery via WhatsApp text.

TONE:
- Proactive, crisp, analytical.
- Narrative over numbers (don't list a table, tell a story).
- No greetings ("Good morning" wastes space).
- Indian context (use 'Rs', 'Lakh', 'K', not '$' or 'M').

STRUCTURE:
Sentence 1: The current state (balance + runway/health).
Sentence 2: The critical insight or alert (what changed or what needs attention).
Sentence 3: The recommended proactive action.

RULES:
1. NEVER hallucinate numbers. Only use the EXACT figures provided in the context.
2. If balance is 150000, write "Rs1.5L". If 25000, write "Rs25K".
3. If there's an alert, prioritize the highest severity alert.
4. DO NOT use bolding or markdown. WhatsApp reads it poorly if converted to voice later.
5. If the health score is < 30, sound urgent. If > 80, sound confident.

JSON OUTPUT:
You must return a JSON object with two fields:
{
  "briefing_text": "Sentence one. Sentence two. Sentence three.",
  "tone": "balanced|urgent|confident"
}
"""

# ================================================================
# BRIEFING GENERATION USER PROMPT BUILDER
# ================================================================

def build_briefing_user_prompt(
    name: str,
    health_score: int,
    balance: float,
    runway_days: int,
    monthly_burn: float,
    monthly_income: float,
    receivables: float,
    alerts: list[dict],
    top_expenses: list[dict],
) -> str:
    """Build the dynamic prompt from the current financial snapshot."""
    
    alert_text = "None"
    if alerts:
        alert_text = "\n".join(
            f"- [{a['severity'].upper()}] {a['alert_type']}: {a['title']}\n  Evidence: {a['evidence']}\n  Action: {a['recommended_action']}"
            for a in alerts
        )

    expense_text = "None"
    if top_expenses:
        expense_text = "\n".join(
            f"- {e['category'].replace('_', ' ').title()}: Rs{e['total_amount']:,.0f} (avg Rs{e['avg_monthly']:,.0f}/mo)"
            for e in top_expenses[:3]
        )

    return f"""Client: {name or 'Founder'}

FINANCIAL SNAPSHOT:
Health Score: {health_score}/100
Current Balance: Rs{balance:,.0f}
Runway: {runway_days} days
Monthly Burn (90d avg): Rs{monthly_burn:,.0f}
Monthly Income (90d avg): Rs{monthly_income:,.0f}
Pending Receivables: Rs{receivables:,.0f}

ACTIVE ALERTS:
{alert_text}

TOP EXPENSE CATEGORIES (90d):
{expense_text}

Generate the 3-sentence daily briefing."""

# ================================================================
# QUALITY CHECK PROMPT
# ================================================================

BRIEFING_QC_PROMPT = """You are a strict compliance checker.
Review the following briefing against the provided financial snapshot data.

RULES:
1. Does it have exactly 3 sentences?
2. Are all numbers in the briefing EXACTLY matching the snapshot data (allowing for K/L abbreviations)?
3. Is it free of hallucinations?

Return JSON:
{
  "passed": true/false,
  "failure_reason": "string explaining what failed, or empty if passed"
}
"""
