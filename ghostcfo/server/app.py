"""
GhostCFO FastAPI Server.

Runs on port 8001. Handles WhatsApp webhooks (Q&A) and exposes AgentOS APIs.
Schedules daily briefings via APScheduler.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from loguru import logger

from ghostcfo.agentOS.financial_context_api import router as agentos_router
from ghostcfo.config import get_settings
from ghostcfo.llm.client import get_llm_client
from ghostcfo.pipeline.qa_graph import build_qa_graph

# Setup scheduler
scheduler = AsyncIOScheduler()
qa_graph = build_qa_graph()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events for FastAPI."""
    settings = get_settings()
    
    # 1. Start LLM Client
    llm = get_llm_client()
    
    # 2. Start Scheduler
    scheduler.start()
    
    # Schedule daily briefing
    hour, minute = settings.ghostcfo_daily_briefing_time.split(":")
    scheduler.add_job(
        scheduled_daily_briefings,
        CronTrigger(hour=int(hour), minute=int(minute), timezone=settings.ghostcfo_timezone),
        id="daily_briefing",
        replace_existing=True,
    )
    
    logger.info("GhostCFO server started on port {}", settings.ghostcfo_port)
    
    yield
    
    # Shutdown
    scheduler.shutdown()
    await llm.close()
    logger.info("GhostCFO server shutdown complete")


app = FastAPI(
    title="GhostCFO",
    description="AgentOS Financial Intelligence Module",
    version="0.1.0",
    lifespan=lifespan,
)

# Include AgentOS router
app.include_router(agentos_router)


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "ghostcfo"}


@app.post("/v1/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    """
    Handle incoming WhatsApp messages for Q&A.
    In a real app, this verifies the webhook signature and parses the payload.
    """
    data = await request.json()
    logger.info("Received WhatsApp webhook")
    # Stub: Normally we'd extract user_id and message, then run:
    # result = await qa_graph.ainvoke({"user_id": user_id, "question": msg, "snapshot": ...})
    return {"status": "received"}


async def scheduled_daily_briefings():
    """Scheduled task to run the briefing pipeline for all users."""
    logger.info("Starting daily briefings batch job")
    # Stub:
    # 1. Query DB for users needing briefing
    # 2. For each user, build snapshot
    # 3. Run briefing_graph
    # 4. Send via WhatsApp / VaakShastra voice API
    pass


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "ghostcfo.server.app:app",
        host="0.0.0.0",
        port=settings.ghostcfo_port,
        reload=settings.environment == "development",
    )
