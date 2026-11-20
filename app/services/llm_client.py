"""The one LLM client used by requirement interpretation and answer writing.

Exactly one provider and one model per deployment: Gemini when
``GEMINI_API_KEY`` is set, otherwise OpenRouter with ``OPENROUTER_MODEL``.
No model cascade: a failure is reported to the caller, which owns the time
budget and the deterministic fallback. Credentials go in headers, never URLs.
"""

from __future__ import annotations

from typing import Literal

import httpx

from app.core import config

Provider = Literal["gemini", "openrouter"]
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def configured_provider() -> Provider | None:
    if config.GEMINI_API_KEY.strip():
        return "gemini"
    if config.OPENROUTER_API_KEY.strip():
        return "openrouter"
    return None


def _request(provider: Provider, system: str, user: str, temperature: float, max_tokens: int):
    if provider == "gemini":
        return (
            GEMINI_URL.format(model=config.GEMINI_MODEL),
            {"x-goog-api-key": config.GEMINI_API_KEY.strip()},
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
            },
        )
    return (
        OPENROUTER_URL,
        {"Authorization": f"Bearer {config.OPENROUTER_API_KEY.strip()}"},
        {
            "model": config.OPENROUTER_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
    )


def _extract_text(provider: Provider, data: dict) -> str:
    if provider == "gemini":
        return data["candidates"][0]["content"]["parts"][0]["text"]
    return data["choices"][0]["message"]["content"]


async def generate(
    system: str,
    user: str,
    *,
    temperature: float = 0.3,
    max_tokens: int | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Return the model text, ``None`` when no provider is configured.

    Raises on transport/HTTP/shape errors; callers decide how to degrade.
    """
    provider = configured_provider()
    if provider is None:
        return None
    url, headers, payload = _request(
        provider, system, user, temperature, max_tokens or config.LLM_MAX_OUTPUT_TOKENS
    )

    async def _post(client: httpx.AsyncClient) -> str:
        response = await client.post(
            url, headers=headers, json=payload, timeout=config.LLM_REQUEST_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return _extract_text(provider, response.json()).strip()

    if http_client is not None:
        return await _post(http_client)
    async with httpx.AsyncClient() as client:
        return await _post(client)
