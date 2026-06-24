# agentos/shared/llm_client.py
# Universal free-tier LLM client with automatic fallback
# Drop this into every agent — zero paid API calls

import os
import time
import httpx
import json
from typing import Optional

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")

FREE_FALLBACK_CHAIN = [
    {"provider": "groq", "model": "gemma2-9b-it"},
    {"provider": "groq", "model": "llama-3.3-70b-versatile"},
    {"provider": "groq", "model": "llama-3.1-8b-instant"},
    {"provider": "ollama", "model": os.getenv("OLLAMA_MODEL", "llama3.2:3b")},
]

class FreeLLMClient:
    """
    Drop-in replacement for all paid LLM calls.
    Tries Groq models in order, falls back to local Ollama.
    Never raises on quota — just falls back.
    """

    async def chat(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> str:
        if system:
            messages = [{"role": "system", "content": system}] + messages

        for tier in FREE_FALLBACK_CHAIN:
            try:
                if tier["provider"] == "groq":
                    result = await self._groq(
                        messages, tier["model"], max_tokens,
                        temperature, json_mode
                    )
                elif tier["provider"] == "ollama":
                    result = await self._ollama(
                        messages, tier["model"], max_tokens
                    )
                if result:
                    return result
            except Exception as e:
                print(f"[LLM] Tier {tier['model']} failed: {e}. Trying next.")
                time.sleep(1)
                continue

        return "[AgentOS: All LLM tiers exhausted. Please retry in 60s.]"

    async def _groq(self, messages, model, max_tokens, temperature, json_mode):
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json=body,
            )
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]

    async def _ollama(self, messages, model, max_tokens):
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json=body,
            )
            r.raise_for_status()
            return r.json()["message"]["content"]


# Singleton — import this everywhere
llm = FreeLLMClient()
