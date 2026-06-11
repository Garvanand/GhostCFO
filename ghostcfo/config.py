"""
GhostCFO Configuration -- Pydantic Settings.

All environment variables for GhostCFO loaded here.
Reuses Day01 LLM keys, shared Redis/Postgres, adds encryption + Gmail OAuth.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """GhostCFO application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -- App --
    environment: Literal["development", "staging", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    ghostcfo_port: int = 8001
    ghostcfo_timezone: str = "Asia/Kolkata"
    ghostcfo_daily_briefing_time: str = "08:00"

    # -- Primary LLM (Anthropic) --
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-4-6-20250219"
    anthropic_daily_budget_usd: float = Field(default=10.0, ge=0)
    anthropic_thinking_budget_complex: int = Field(default=6000, ge=1000)
    anthropic_thinking_budget_simple: int = Field(default=2000, ge=500)

    # -- Fallback LLM 1 (Groq) --
    groq_api_key: str = ""
    groq_llm_model: str = "openai/gpt-oss-120b"
    groq_llm_model_fast: str = "llama-3.1-8b-instant"

    # -- Fallback LLM 2 (OpenRouter) --
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.5-pro"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str = "https://ghostcfo.agentOS"
    openrouter_app_name: str = "GhostCFO"

    # -- Encryption --
    ghostcfo_encryption_master_key: str = ""

    # -- WhatsApp (shared with VaakShastra) --
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_verify_token: str = "ghostcfo_verify_2024"
    whatsapp_api_version: str = "v19.0"

    # -- VaakShastra AgentOS endpoint --
    vaakshastra_base_url: str = "http://localhost:8000"

    # -- Gmail OAuth --
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    gmail_redirect_uri: str = "https://yourdomain.com/v1/oauth/gmail/callback"

    # -- Database & Cache --
    redis_url: str = "redis://localhost:6379/0"
    database_url: str = "postgresql+asyncpg://vaakshastra:vaakshastra@localhost:5432/vaakshastra"

    # -- GST --
    gstn_api_key: str = ""

    # -- Derived --
    @property
    def whatsapp_base_url(self) -> str:
        return f"https://graph.facebook.com/{self.whatsapp_api_version}"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton settings instance."""
    return Settings()
