"""
GhostCFO Shared Memory Writer.

Writes privacy-safe financial context to the shared Redis namespace
so other AgentOS agents (like SoulMap or NexusOps) can adapt their
behavior based on the user's financial stress/runway.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from loguru import logger

from ghostcfo.config import get_settings
from ghostcfo.models.snapshot import FinancialSnapshot


async def update_agent_os_memory(snapshot: FinancialSnapshot) -> None:
    """
    Write privacy-safe financial state to AgentOS shared memory.
    
    Instead of writing exact numbers (which violates privacy), we write
    bands and scores so other agents know the *context* without seeing
    the bank balance.
    """
    settings = get_settings()
    
    try:
        import redis.asyncio as aioredis
        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        await redis.ping()
    except Exception as exc:
        logger.warning("Could not connect to Redis for AgentOS memory update: {}", exc)
        return
        
    try:
        # Determine stress level
        if snapshot.runway_days < 14:
            stress_level = "CRITICAL"
        elif snapshot.runway_days < 30:
            stress_level = "HIGH"
        elif snapshot.runway_days < 90:
            stress_level = "MODERATE"
        else:
            stress_level = "LOW"
            
        # Determine runway band
        if snapshot.runway_days > 180:
            runway_band = ">6 months"
        elif snapshot.runway_days > 90:
            runway_band = "3-6 months"
        elif snapshot.runway_days > 30:
            runway_band = "1-3 months"
        else:
            runway_band = "<1 month"
            
        memory_data = {
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "health_score": snapshot.health_score,
            "financial_stress": stress_level,
            "runway_band": runway_band,
            "has_overdue_receivables": float(snapshot.overdue_receivables) > 0,
            "is_cashflow_positive": float(snapshot.monthly_income_rate) > float(snapshot.monthly_burn_rate)
        }
        
        # Write to shared namespace
        key = f"agentOS:memory:financial:{snapshot.user_id}"
        await redis.set(key, json.dumps(memory_data))
        # Keep context valid for 48 hours max without an update
        await redis.expire(key, 172800)
        
        logger.info("AgentOS shared memory updated for user={}", snapshot.user_id)
    except Exception as exc:
        logger.error("Failed to update AgentOS memory: {}", exc)
    finally:
        await redis.close()
