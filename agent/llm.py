"""
Provider seam for the agent.

This module is the *sole* provider-coupling point: it is the agent runtime's
sole coupling point; it imports the provider SDK (``anthropic``) — and that
includes budget-error classification. A provider swap must map the new provider's
429-class retry-exhaustion error to ``ProviderBudgetError`` here and touch
nothing else; the rest of the codebase reacts only to the provider-agnostic
exception.
"""

from __future__ import annotations

import threading
import time

import anthropic

from agent.config import LLM_MAX_RETRIES, MAX_TOKENS, MODEL, TEMPERATURE, api_key

_client: anthropic.Anthropic | None = None
_client_lock = threading.Lock()

# Delay schedule (seconds) for successive retry attempts: 1s, 2s, 4s, …
_BACKOFF_BASE = 1.0


class ProviderBudgetError(Exception):
    """Raised when retries are exhausted on a 429-class provider error
    (spend cap or sustained throttling). Provider-agnostic signal;
    detection of the provider-specific exception stays in this module."""


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # Double-checked locking: worker threads may race on first use.
        with _client_lock:
            if _client is None:
                _client = anthropic.Anthropic(api_key=api_key())
    return _client


def call(
    system_prompt: str,
    tools: list[dict],
    messages: list[dict],
    *,
    max_tokens: int | None = None,
    model: str | None = None,
) -> anthropic.types.Message:
    """Call the model with exponential backoff retry on provider 429/5xx."""
    cached_tools = (
        [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
        if tools
        else tools
    )
    system = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]
    effective_max_tokens = MAX_TOKENS if max_tokens is None else max_tokens
    effective_model = model if model is not None else MODEL

    last_exc: Exception | None = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            return _get_client().messages.create(
                model=effective_model,
                max_tokens=effective_max_tokens,
                temperature=TEMPERATURE,
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
            delay = _BACKOFF_BASE * (2**attempt)
            time.sleep(delay)

    if isinstance(last_exc, anthropic.RateLimitError):
        raise ProviderBudgetError(
            f"LLM call failed after {LLM_MAX_RETRIES} retries. Last error: {last_exc}"
        ) from last_exc

    raise RuntimeError(
        f"LLM call failed after {LLM_MAX_RETRIES} retries. Last error: {last_exc}"
    ) from last_exc
