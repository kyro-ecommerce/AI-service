import asyncio
from unittest.mock import patch

from app.services.chat_service import _run_llm_with_budget


def test_provider_timeout_returns_control_to_fallback_pipeline() -> None:
    async def scenario() -> str | None:
        async def slow_provider() -> str:
            await asyncio.sleep(0.2)
            return "late response"

        deadline = asyncio.get_running_loop().time() + 1.0
        with patch("app.services.chat_service.LLM_REQUEST_TIMEOUT_SECONDS", 0.01):
            return await _run_llm_with_budget(
                slow_provider,
                deadline=deadline,
                provider="test-provider",
            )

    assert asyncio.run(scenario()) is None


def test_exhausted_total_budget_does_not_start_provider() -> None:
    provider_started = False

    async def scenario() -> str | None:
        async def provider() -> str:
            nonlocal provider_started
            provider_started = True
            return "unexpected"

        expired_deadline = asyncio.get_running_loop().time() - 1.0
        return await _run_llm_with_budget(
            provider,
            deadline=expired_deadline,
            provider="test-provider",
        )

    assert asyncio.run(scenario()) is None
    assert not provider_started
