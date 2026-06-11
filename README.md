# GhostCFO - AgentOS Day 02

GhostCFO is the proactive financial intelligence module for AgentOS. It acts as an elite AI CFO for Indian solo founders and freelancers, analyzing bank statements, tracking runway, and surfacing insights before they become crises.

## Core Capabilities
- **Multi-Bank PDF Parsing:** Supports HDFC, ICICI, SBI, and Axis via `pdfplumber` and `PyMuPDF`, with a Claude Vision fallback for scanned images.
- **Proactive Intelligence:** Cash flow engine projects balances up to 30 days forward across 3 scenarios (pessimistic, base, optimistic).
- **Proactive Alerts:** Detects cash cliffs (<21 days), rate anomalies (>20% income drop), and expense spikes.
- **Briefing Generator:** A LangGraph pipeline that synthesizes the financial snapshot into a crisp, exactly 3-sentence narrative.
- **AgentOS Shared Memory:** Extracts privacy-safe 'stress bands' and shares them with other agents via Redis.

## Architecture Highlights
- **Privacy First:** Per-user Fernet encryption via HKDF for all sensitive transaction fields (`description`, `counterparty`).
- **3-Tier Classification:** 
  1. Regex rules (~60% of volume, zero cost)
  2. Groq batch LLM (fast, cheap)
  3. Claude 3.5 Sonnet (complex edge cases)
- **Frameworks:** FastAPI, LangGraph, PostgreSQL (Partitioned), Redis.

## Getting Started

1. Create a Master Encryption Key:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
2. Add the key to your `.env` file along with your API keys (see `.env.example`).
3. Run the complete AgentOS stack:
```bash
docker-compose up --build
```
GhostCFO runs on port `8001`. VaakShastra runs on `8000`.
