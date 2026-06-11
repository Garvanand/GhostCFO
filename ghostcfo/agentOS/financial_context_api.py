"""
GhostCFO Agent-to-Agent API.

FastAPI router exposing GhostCFO's intelligence to other AgentOS agents.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

# In a real implementation, this would query the DB.
# We'll stub it to return a mock response for the MVP.

router = APIRouter(prefix="/v1/agentOS/financial", tags=["AgentOS"])

class AffordabilityRequest(BaseModel):
    amount: float
    description: str

class AffordabilityResponse(BaseModel):
    can_afford: bool
    impact_on_runway_days: int
    advice: str

class FinancialContextResponse(BaseModel):
    health_score: int
    stress_level: str
    runway_band: str

@router.get("/context/{user_id}", response_model=FinancialContextResponse)
async def get_financial_context(user_id: str):
    """
    Get privacy-safe financial context for a user.
    Used by agents like SoulMap to adjust their tone based on user's financial stress.
    """
    # MVP Stub: return a moderate profile
    return FinancialContextResponse(
        health_score=75,
        stress_level="LOW",
        runway_band="3-6 months"
    )

@router.post("/affordability/{user_id}", response_model=AffordabilityResponse)
async def check_affordability(user_id: str, request: AffordabilityRequest):
    """
    Check if a user can afford an expense.
    Used by Legal/Scheduling agents before committing to paid services.
    """
    # MVP Stub logic
    if request.amount > 50000:
        return AffordabilityResponse(
            can_afford=False,
            impact_on_runway_days=-15,
            advice="This is a major expense that drops runway below 90 days. Defer if possible."
        )
    return AffordabilityResponse(
        can_afford=True,
        impact_on_runway_days=-2,
        advice="Expense is within safe operational limits."
    )

@router.post("/expense_recorded")
async def record_expense(user_id: str = Query(...), amount: float = Query(...), description: str = Query(...)):
    """
    Allow other agents to record an expense directly.
    """
    # Would insert into DB
    return {"status": "recorded", "transaction_id": "txn_mock_123"}
