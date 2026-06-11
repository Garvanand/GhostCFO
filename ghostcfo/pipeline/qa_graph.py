"""
GhostCFO Conversational Q&A Pipeline.

LangGraph pipeline for answering user questions on demand via WhatsApp.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, StateGraph
from loguru import logger

from ghostcfo.llm.client import get_llm_client
from ghostcfo.models.snapshot import FinancialSnapshot
from prompts.qa_answerer import (
    QA_ANSWER_SYSTEM_PROMPT,
    QA_CLASSIFICATION_PROMPT,
    build_qa_context_prompt,
)


class QAState(TypedDict):
    """State for the Q&A graph."""
    user_id: str
    question: str
    snapshot: FinancialSnapshot
    
    # Internal state
    intent: str
    context_data: dict[str, Any]
    add_proactive_insight: bool
    
    # Output
    final_answer: str
    error: str | None


async def node_classify_intent(state: QAState) -> dict[str, Any]:
    """Classify the user's question into one of 9 categories."""
    llm = get_llm_client()
    
    try:
        resp = await llm.complete(
            messages=[{"role": "user", "content": state["question"]}],
            system=QA_CLASSIFICATION_PROMPT,
            task_type="general",
            max_tokens=10,
            temperature=0.0,
        )
        intent = resp.content.strip().upper()
        # Clean up any unexpected text
        for valid in ["BALANCE", "RATE", "CLIENT", "AFFORDABILITY", "TAX", "INVOICE", "TREND", "FORECAST", "UNKNOWN"]:
            if valid in intent:
                return {"intent": valid}
                
        return {"intent": "UNKNOWN"}
    except Exception as exc:
        logger.error("QA Classification failed: {}", exc)
        return {"intent": "UNKNOWN", "error": str(exc)}


async def node_gather_context(state: QAState) -> dict[str, Any]:
    """
    Gather context specific to the intent.
    In a full DB-backed implementation, this would query Postgres based on intent.
    For this MVP graph, we just extract relevant parts of the snapshot.
    """
    snapshot = state["snapshot"]
    intent = state["intent"]
    
    context_data = {
        "snapshot_summary": (
            f"Balance: Rs{snapshot.current_balance:,.0f}\n"
            f"Runway: {snapshot.runway_days} days\n"
            f"Burn: Rs{snapshot.monthly_burn_rate:,.0f}/mo\n"
            f"Income: Rs{snapshot.monthly_income_rate:,.0f}/mo\n"
            f"Receivables: Rs{snapshot.total_receivables:,.0f}"
        ),
        "relevant_transactions": "Data aggregation simulated based on snapshot.",
        "alerts": "No critical alerts." # Would be fetched from DB
    }
    
    # Add specific details based on intent
    if intent == "TAX":
        context_data["snapshot_summary"] += f"\nEst GST: Rs{snapshot.gst_liability_estimate:,.0f}"
        context_data["snapshot_summary"] += f"\nEst TDS: Rs{snapshot.tds_liability_estimate:,.0f}"
    elif intent == "TREND":
        cats = [f"{e.category.value}: Rs{e.total_amount:,.0f}" for e in snapshot.top_expense_categories]
        context_data["relevant_transactions"] = "Top Expenses (90d):\n" + "\n".join(cats)
        
    return {"context_data": context_data}


async def node_generate_answer(state: QAState) -> dict[str, Any]:
    """Generate the answer using Claude."""
    llm = get_llm_client()
    
    if state["intent"] == "UNKNOWN":
        return {
            "final_answer": "I'm your AI CFO. I can help with your balances, cash flow, invoices, and affordability analysis, but I can't answer that specific question.",
            "add_proactive_insight": False
        }
        
    user_msg = build_qa_context_prompt(
        question=state["question"],
        snapshot_data=state["context_data"]["snapshot_summary"],
        relevant_transactions=state["context_data"]["relevant_transactions"],
        alerts=state["context_data"]["alerts"],
    )
    
    # Use thinking model for affordability (complex logic required)
    use_thinking = state["intent"] in ("AFFORDABILITY", "FORECAST", "TREND")
    
    try:
        resp = await llm.complete(
            messages=[{"role": "user", "content": user_msg}],
            system=QA_ANSWER_SYSTEM_PROMPT,
            task_type="qa_answer",
            use_thinking=use_thinking,
            thinking_budget=2000 if use_thinking else 0,
            max_tokens=800,
            temperature=0.2,
        )
        
        content = resp.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            
        data = json.loads(content)
        return {
            "final_answer": data.get("answer", "I couldn't process your request right now."),
            "add_proactive_insight": data.get("add_proactive_insight", False)
        }
    except Exception as exc:
        logger.error("QA Answer generation failed: {}", exc)
        return {
            "final_answer": f"Sorry, I ran into an issue calculating that for you right now.",
            "error": str(exc)
        }


async def node_add_insight(state: QAState) -> dict[str, Any]:
    """Optionally append a proactive insight if the LLM requested it."""
    if state["add_proactive_insight"] and state["snapshot"].runway_days < 30:
        insight = "\n\n*Proactive Note:* Your runway is under 30 days. Let me know if you want to review outstanding invoices we can collect."
        return {"final_answer": state["final_answer"] + insight}
    return {}


def build_qa_graph() -> StateGraph:
    """Build the LangGraph pipeline for conversational Q&A."""
    workflow = StateGraph(QAState)
    
    workflow.add_node("classify", node_classify_intent)
    workflow.add_node("context", node_gather_context)
    workflow.add_node("generate", node_generate_answer)
    workflow.add_node("insight", node_add_insight)
    
    workflow.set_entry_point("classify")
    workflow.add_edge("classify", "context")
    workflow.add_edge("context", "generate")
    workflow.add_edge("generate", "insight")
    workflow.add_edge("insight", END)
    
    return workflow.compile()
