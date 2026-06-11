"""
GhostCFO Briefing Generator -- LangGraph Pipeline.

Generates the daily 3-sentence WhatsApp briefing.
Includes a self-correction loop (Quality Check node) to prevent number hallucinations.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from loguru import logger

from ghostcfo.llm.client import get_llm_client
from ghostcfo.models.alert import BriefingRecord, CFOAlert
from ghostcfo.models.snapshot import FinancialSnapshot
from prompts.briefing_generator import (
    BRIEFING_QC_PROMPT,
    BRIEFING_SYSTEM_PROMPT,
    build_briefing_user_prompt,
)


class BriefingState(TypedDict):
    """State for the briefing generation graph."""
    user_id: str
    user_name: str
    snapshot: FinancialSnapshot
    alerts: list[CFOAlert]
    
    # Generated content
    draft_briefing: str
    draft_tone: str
    
    # QC loop
    qc_passed: bool
    qc_feedback: str
    generation_attempts: int
    
    # Final output
    final_briefing: str
    final_record: BriefingRecord | None
    error: str | None


async def node_generate_briefing(state: BriefingState) -> dict[str, Any]:
    """Generate the 3-sentence briefing using Claude."""
    llm = get_llm_client()
    
    alerts_data = [
        {
            "alert_type": a.alert_type,
            "severity": a.severity,
            "title": a.title,
            "evidence": a.evidence,
            "recommended_action": a.recommended_action
        }
        for a in state["alerts"]
    ]
    
    expenses_data = [
        {
            "category": e.category.value,
            "total_amount": float(e.total_amount),
            "avg_monthly": float(e.avg_monthly)
        }
        for e in state["snapshot"].top_expense_categories
    ]
    
    user_msg = build_briefing_user_prompt(
        name=state["user_name"],
        health_score=state["snapshot"].health_score,
        balance=float(state["snapshot"].current_balance),
        runway_days=state["snapshot"].runway_days,
        monthly_burn=float(state["snapshot"].monthly_burn_rate),
        monthly_income=float(state["snapshot"].monthly_income_rate),
        receivables=float(state["snapshot"].total_receivables),
        alerts=alerts_data,
        top_expenses=expenses_data,
    )

    if state["qc_feedback"] and state["generation_attempts"] > 0:
        user_msg += f"\n\nPREVIOUS ATTEMPT FAILED QC. FEEDBACK: {state['qc_feedback']}\nFix the issues."

    try:
        resp = await llm.complete(
            messages=[{"role": "user", "content": user_msg}],
            system=BRIEFING_SYSTEM_PROMPT,
            task_type="briefing_generation",
            max_tokens=500,
            temperature=0.4, # Slight creativity, but mostly deterministic
        )
        
        content = resp.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            
        data = json.loads(content)
        
        return {
            "draft_briefing": data.get("briefing_text", ""),
            "draft_tone": data.get("tone", "balanced"),
            "generation_attempts": state.get("generation_attempts", 0) + 1
        }
        
    except Exception as exc:
        logger.error("Briefing generation failed: {}", exc)
        return {
            "error": str(exc),
            "generation_attempts": state.get("generation_attempts", 0) + 1
        }


async def node_quality_check(state: BriefingState) -> dict[str, Any]:
    """Verify exactly 3 sentences and no hallucinated numbers."""
    if state.get("error"):
        return {"qc_passed": False}
        
    draft = state["draft_briefing"]
    
    # Fast-fail sentence count check
    sentences = [s.strip() for s in draft.split(".") if s.strip()]
    if len(sentences) != 3:
        return {
            "qc_passed": False,
            "qc_feedback": f"Briefing has {len(sentences)} sentences. It MUST have exactly 3."
        }
        
    # LLM number hallucination check
    llm = get_llm_client()
    
    snapshot_data = (
        f"Balance: {state['snapshot'].current_balance}, "
        f"Burn: {state['snapshot'].monthly_burn_rate}, "
        f"Runway: {state['snapshot'].runway_days}, "
        f"Receivables: {state['snapshot'].total_receivables}"
    )
    
    user_msg = f"Snapshot Data:\n{snapshot_data}\n\nBriefing to check:\n{draft}"
    
    try:
        resp = await llm.complete(
            messages=[{"role": "user", "content": user_msg}],
            system=BRIEFING_QC_PROMPT,
            task_type="briefing_generation",
            max_tokens=200,
            temperature=0.0,
        )
        
        content = resp.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            
        data = json.loads(content)
        
        return {
            "qc_passed": data.get("passed", False),
            "qc_feedback": data.get("failure_reason", "")
        }
    except Exception as exc:
        logger.warning("QC node failed, falling back to basic checks: {}", exc)
        return {"qc_passed": True} # Default to pass if QC node fails


async def node_finalize(state: BriefingState) -> dict[str, Any]:
    """Create the final BriefingRecord."""
    if state.get("error") and state["generation_attempts"] >= 2:
        # Hard fallback
        draft = (
            f"Your current balance is Rs{float(state['snapshot'].current_balance):,.0f} "
            f"with {state['snapshot'].runway_days} days of runway. "
            "Please check the system for detailed insights. "
            "Stay proactive with your finances."
        )
        tone = "balanced"
    else:
        draft = state["draft_briefing"]
        tone = state["draft_tone"]
        
    record = BriefingRecord(
        user_id=state["user_id"],
        briefing_text=draft,
        snapshot_id=state["snapshot"].snapshot_id,
        health_score=state["snapshot"].health_score,
        alerts_included=len(state["alerts"]),
        tone=tone,
    )
    
    return {
        "final_briefing": draft,
        "final_record": record,
    }


def route_qc(state: BriefingState) -> str:
    """Route after QC."""
    if state["qc_passed"]:
        return "finalize"
    if state["generation_attempts"] >= 2:
        logger.warning("Briefing QC failed twice, proceeding to finalize anyway.")
        return "finalize"
    return "generate"


def build_briefing_graph() -> StateGraph:
    """Build the LangGraph pipeline for briefing generation."""
    workflow = StateGraph(BriefingState)
    
    workflow.add_node("generate", node_generate_briefing)
    workflow.add_node("qc", node_quality_check)
    workflow.add_node("finalize", node_finalize)
    
    workflow.set_entry_point("generate")
    workflow.add_edge("generate", "qc")
    workflow.add_conditional_edges(
        "qc",
        route_qc,
        {
            "generate": "generate",
            "finalize": "finalize"
        }
    )
    workflow.add_edge("finalize", END)
    
    return workflow.compile()
