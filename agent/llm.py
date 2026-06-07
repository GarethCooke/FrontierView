from __future__ import annotations

import time

import anthropic

from agent.config import LLM_MAX_RETRIES, MAX_TOKENS, MODEL, api_key

_client: anthropic.Anthropic | None = None

# Delay schedule (seconds) for successive retry attempts: 1s, 2s, 4s, …
_BACKOFF_BASE = 1.0


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=api_key())
    return _client


def call(
    system_prompt: str,
    tools: list[dict],
    messages: list[dict],
) -> anthropic.types.Message:
    """Call the model with exponential backoff retry on provider 429/5xx."""
    cached_tools = (
        [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
        if tools
        else tools
    )
    system = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]

    last_exc: Exception | None = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            return _get_client().messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=system,  # type: ignore[arg-type]
                tools=cached_tools,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
            )
        except anthropic.RateLimitError as exc:
            last_exc = exc
        except anthropic.APIStatusError as exc:
            if exc.status_code < 500:
                raise  # 4xx other than 429 — don't retry
            last_exc = exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            last_exc = exc

        if attempt < LLM_MAX_RETRIES - 1:
            delay = _BACKOFF_BASE * (2 ** attempt)
            time.sleep(delay)

    raise RuntimeError(
        f"LLM call failed after {LLM_MAX_RETRIES} retries. Last error: {last_exc}"
    ) from last_exc
