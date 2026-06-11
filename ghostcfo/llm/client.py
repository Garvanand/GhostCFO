"""
GhostCFO LLM Client -- Adapted from VaakShastra Day01.

Same 4-tier fallback chain, rebranded namespace for cost tracking.
Adds task_types specific to financial intelligence.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import anthropic
import httpx
from groq import AsyncGroq
from loguru import logger
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ghostcfo.config import get_settings

COST_TABLE: dict[str, dict[str, float]] = {
    "claude-opus-4-6-20250219": {"input": 15.0, "output": 75.0},
    "openai/gpt-oss-120b": {"input": 0.0, "output": 0.0},  # Free on Groq
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    "llama-3.1-8b-instant": {"input": 0.05, "output": 0.08},
    "google/gemini-2.5-pro": {"input": 1.25, "output": 10.0},
}


@dataclass
class LLMResponse:
    content: str
    model_used: str
    provider: Literal["anthropic", "groq", "openrouter"]
    thinking_content: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    fallback_level: int = 0
    cost_usd: float = 0.0
    raw_response: Optional[dict[str, Any]] = field(default=None, repr=False)


class LLMClient:
    """Unified LLM client with automatic failover for GhostCFO."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._anthropic: Optional[anthropic.AsyncAnthropic] = None
        self._groq: Optional[AsyncGroq] = None
        self._httpx: Optional[httpx.AsyncClient] = None
        self._redis: Any = None

    async def complete(
        self,
        messages: list[dict[str, str]],
        system: str,
        task_type: Literal[
            "transaction_classification",
            "briefing_generation",
            "qa_answer",
            "invoice_extraction",
            "macro_extraction",
            "general",
        ] = "general",
        use_thinking: bool = False,
        thinking_budget: int = 4000,
        max_tokens: int = 1024,
        temperature: float = 0.1,
    ) -> LLMResponse:
        errors: list[str] = []

        # Level 0: Anthropic
        if self._settings.anthropic_api_key and not await self._budget_exceeded():
            try:
                return await self._call_anthropic(
                    messages, system, use_thinking, thinking_budget,
                    max_tokens, temperature, 0,
                )
            except Exception as exc:
                errors.append(f"anthropic: {exc!r}")
                logger.warning("Anthropic failed -> Groq | {}", exc)

        # Level 1: Groq primary
        if self._settings.groq_api_key:
            try:
                return await self._call_groq(
                    messages, system, self._settings.groq_llm_model,
                    max_tokens, temperature, 1,
                )
            except Exception as exc:
                errors.append(f"groq: {exc!r}")
                logger.warning("Groq failed -> OpenRouter | {}", exc)

        # Level 2: OpenRouter
        if self._settings.openrouter_api_key:
            try:
                return await self._call_openrouter(
                    messages, system, max_tokens, temperature, 2,
                )
            except Exception as exc:
                errors.append(f"openrouter: {exc!r}")
                logger.warning("OpenRouter failed -> Groq 8B | {}", exc)

        # Level 3: Groq fast (last resort)
        if self._settings.groq_api_key:
            try:
                return await self._call_groq(
                    messages, system, self._settings.groq_llm_model_fast,
                    max_tokens, temperature, 3,
                )
            except Exception as exc:
                errors.append(f"groq-8b: {exc!r}")

        raise RuntimeError(f"All LLM providers failed: {'; '.join(errors)}")

    async def _call_anthropic(self, messages, system, use_thinking,
                               thinking_budget, max_tokens, temperature, level) -> LLMResponse:
        client = self._get_anthropic_client()
        start = time.perf_counter()

        kwargs: dict[str, Any] = {
            "model": self._settings.anthropic_model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if use_thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": thinking_budget}
            kwargs["temperature"] = 1.0
        else:
            kwargs["temperature"] = temperature

        response = await client.messages.create(**kwargs)
        latency_ms = int((time.perf_counter() - start) * 1000)

        content_text, thinking_text = "", ""
        for block in response.content:
            if block.type == "thinking":
                thinking_text += block.thinking
            elif block.type == "text":
                content_text += block.text

        cost = self._calc_cost(self._settings.anthropic_model,
                               response.usage.input_tokens, response.usage.output_tokens)
        await self._record_cost("anthropic", cost)

        logger.info("Anthropic | tokens_in={} out={} cost=${:.4f} latency={}ms",
                     response.usage.input_tokens, response.usage.output_tokens, cost, latency_ms)

        return LLMResponse(
            content=content_text, model_used=self._settings.anthropic_model,
            provider="anthropic", thinking_content=thinking_text or None,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms, fallback_level=level, cost_usd=cost,
        )

    async def _call_groq(self, messages, system, model, max_tokens, temperature, level) -> LLMResponse:
        client = self._get_groq_client()
        start = time.perf_counter()
        full = [{"role": "system", "content": system}] + messages

        response = await client.chat.completions.create(
            model=model, messages=full, max_tokens=max_tokens, temperature=temperature,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        usage = response.usage
        cost = self._calc_cost(model, usage.prompt_tokens, usage.completion_tokens)
        await self._record_cost("groq", cost)

        logger.info("Groq | model={} tokens_in={} out={} cost=${:.6f} latency={}ms",
                     model, usage.prompt_tokens, usage.completion_tokens, cost, latency_ms)

        return LLMResponse(
            content=response.choices[0].message.content or "",
            model_used=model, provider="groq",
            input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens,
            latency_ms=latency_ms, fallback_level=level, cost_usd=cost,
        )

    async def _call_openrouter(self, messages, system, max_tokens, temperature, level) -> LLMResponse:
        client = self._get_httpx_client()
        start = time.perf_counter()
        full = [{"role": "system", "content": system}] + messages

        response = await client.post(
            f"{self._settings.openrouter_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._settings.openrouter_api_key}",
                "HTTP-Referer": self._settings.openrouter_site_url,
                "X-Title": self._settings.openrouter_app_name,
                "Content-Type": "application/json",
            },
            json={
                "model": self._settings.openrouter_model,
                "messages": full, "max_tokens": max_tokens, "temperature": temperature,
            },
            timeout=60.0,
        )
        response.raise_for_status()
        data = response.json()
        latency_ms = int((time.perf_counter() - start) * 1000)

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        inp, out = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        cost = self._calc_cost(self._settings.openrouter_model, inp, out)
        await self._record_cost("openrouter", cost)

        logger.info("OpenRouter | model={} tokens_in={} out={} cost=${:.4f} latency={}ms",
                     self._settings.openrouter_model, inp, out, cost, latency_ms)

        return LLMResponse(
            content=content, model_used=self._settings.openrouter_model,
            provider="openrouter", input_tokens=inp, output_tokens=out,
            latency_ms=latency_ms, fallback_level=level, cost_usd=cost,
        )

    def _calc_cost(self, model: str, inp: int, out: int) -> float:
        rates = COST_TABLE.get(model, {"input": 0.0, "output": 0.0})
        return (inp * rates["input"] + out * rates["output"]) / 1_000_000

    async def _record_cost(self, provider: str, cost: float) -> None:
        try:
            redis = await self._get_redis()
            if not redis:
                return
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            key = f"ghostcfo:cost:{today}:{provider}"
            await redis.incrbyfloat(key, cost)
            await redis.expire(key, 86400 * 7)
        except Exception:
            pass

    async def _budget_exceeded(self) -> bool:
        try:
            redis = await self._get_redis()
            if not redis:
                return False
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            total = float(await redis.get(f"ghostcfo:cost:{today}:anthropic") or 0)
            return total >= self._settings.anthropic_daily_budget_usd
        except Exception:
            return False

    def _get_anthropic_client(self):
        if not self._anthropic:
            self._anthropic = anthropic.AsyncAnthropic(
                api_key=self._settings.anthropic_api_key, timeout=30.0)
        return self._anthropic

    def _get_groq_client(self):
        if not self._groq:
            self._groq = AsyncGroq(api_key=self._settings.groq_api_key)
        return self._groq

    def _get_httpx_client(self):
        if not self._httpx:
            self._httpx = httpx.AsyncClient(timeout=60.0)
        return self._httpx

    async def _get_redis(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self._settings.redis_url, decode_responses=True)
                await self._redis.ping()
            except Exception:
                self._redis = None
        return self._redis

    async def close(self):
        if self._httpx: await self._httpx.aclose()
        if self._redis: await self._redis.close()


_client: Optional[LLMClient] = None

def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
