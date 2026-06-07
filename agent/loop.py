"""
Agent execution loop.

Recovery-policy table (§4 of the Phase 2 brief):

  Error class                               Surface      Handler          Action                                    Retry budget
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  Invalid tool arg / unknown sweep param    tool-error   dispatch()       structured error dict → model             ~2 self-corrects
  Numerical blow-up / order ≫ ADV           tool-error   tool layer       structured warning + unreliable=True       n/a (warn-and-proceed)
  Unknown tool / malformed tool-use JSON    loop         _resolve_block   structured error with valid-tool list      bounded
  Provider 429 / timeout / 5xx             loop         llm.call()       exponential backoff (llm.py)               LLM_MAX_RETRIES
  Over-length response (max_tokens)        loop         harness          trigger compaction, retry                  capped (MAX_ITERS)
  Repeated identical tool call             loop         _is_duplicate    nudge once, then graceful abort            1 nudge
"""
from __future__ import annotations

import hashlib
import json

from agent import llm, tools, trace
from agent.compaction import compact_messages, should_compact
from agent.config import MAX_ITERS, MAX_TOKENS, TOOL_RETRY_BUDGET

_MAX_TRUNCATION_RETRIES = 2
_TRUNCATION_RAISED_BUDGET = min(MAX_TOKENS * 2, 8192)

_SYSTEM_PROMPT = """You are a quantitative analyst assistant for FrontierView, \
a pre-trade market impact analysis tool based on the Almgren-Chriss (2005) model.

Answer questions about optimal trade execution by calling the available tools.
Symbol reference values are STORED DATA, not a live feed — always caveat accordingly.

Tool overview:
- cost_and_variance: cost & variance for a single execution schedule
- optimal_schedule: Almgren-Chriss optimal schedule at a given risk-aversion λ
- compare_schedules: side-by-side comparison of multiple schedules (preferred over repeated cost calls)
- efficient_frontier: cost/variance frontier across a λ grid
- sweep: sensitivity analysis — vary one model parameter over a range
- list_symbols: available symbols (stored reference values)
- get_symbol_reference: stored reference values for a symbol (ADV, σ, spread)
- describe_model: model parameters and their Almgren 2005 provenance

Always use tool calls to retrieve numeric results — do not estimate or calculate \
market impact values yourself. Caveat sweep results on structural parameters (e.g. \
the 0.6 temporary-impact exponent) as changing model identity. When you have enough \
information, give a clear, concise answer in plain English with the key numbers."""


def run(question: str) -> str:
    """Run the agent loop and return the final answer."""
    messages: list[dict] = [{"role": "user", "content": question}]
    response = None

    # Per-tool retry tracking: tool_key → error count
    retry_counts: dict[str, int] = {}
    # Duplicate-call tracking: tool_key → first-seen turn
    seen_calls: dict[str, int] = {}
    duplicate_nudged: set[str] = set()  # keys that have already been nudged once

    truncation_retries = 0
    current_max_tokens: int | None = None  # None = use default MAX_TOKENS

    for iteration in range(MAX_ITERS):

        # Compact if the transcript has grown too large (before sending to model)
        if should_compact(messages):
            messages = compact_messages(messages)
            trace.step("COMPACTION", f"Transcript compacted at iteration {iteration}")

        response = llm.call(_SYSTEM_PROMPT, tools.TOOLS, messages, max_tokens=current_max_tokens)

        text_parts = [b.text for b in response.content if b.type == "text"]
        if text_parts:
            trace.step("REASONING", "\n".join(text_parts))

        # Over-length output: raise budget and retry; return truncated after cap
        if response.stop_reason == "max_tokens":
            if should_compact(messages):
                messages = compact_messages(messages)
            truncation_retries += 1
            if truncation_retries > _MAX_TRUNCATION_RETRIES:
                partial = "\n".join(text_parts)
                note = "[response truncated]"
                trace.step("MAX_TOKENS", "Truncation cap exceeded; returning partial answer")
                return f"{partial}\n{note}" if partial else note
            current_max_tokens = _TRUNCATION_RAISED_BUDGET
            trace.step(
                "MAX_TOKENS",
                f"Response truncated; raised output budget to {current_max_tokens} "
                f"(retry {truncation_retries}/{_MAX_TRUNCATION_RETRIES})",
            )
            continue

        if response.stop_reason != "tool_use":
            answer = "\n".join(text_parts)
            if not answer:
                answer = f"[No text produced; stop_reason={response.stop_reason!r}]"
            trace.step("FINAL ANSWER", answer)
            return answer

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_key = _call_key(block.name, block.input)
            trace.step("TOOL CALL", {"name": block.name, "args": block.input})

            # Duplicate-call guard
            if tool_key in seen_calls:
                if tool_key not in duplicate_nudged:
                    duplicate_nudged.add(tool_key)
                    trace.step("DUPLICATE", f"Repeated call to '{block.name}'; nudging model")
                    result = {
                        "error": "DuplicateCall",
                        "detail": (
                            f"You already called '{block.name}' with these exact arguments "
                            "(the result was established earlier). "
                            "Please use the established result and proceed to your final answer."
                        ),
                    }
                else:
                    # This tool has already been nudged once — abort gracefully
                    trace.step("ABORT", f"Repeated duplicate call to '{block.name}'; aborting")
                    last_text = "\n".join(b.text for b in response.content if b.type == "text")
                    return (
                        "Aborted: the model repeatedly called the same tool with identical "
                        f"arguments ('{block.name}'). Partial answer: {last_text}"
                    )
            else:
                seen_calls[tool_key] = iteration
                result = tools.dispatch(block.name, block.input)

            # Retry-budget enforcement for tool errors
            if "error" in result:
                retry_counts[tool_key] = retry_counts.get(tool_key, 0) + 1
                if retry_counts[tool_key] > TOOL_RETRY_BUDGET:
                    result = {
                        **result,
                        "retry_budget_exhausted": True,
                        "detail": (
                            result.get("detail", "")
                            + " [Retry budget exhausted — do not call this tool again with these arguments.]"
                        ),
                    }

            trace.step("TOOL RESULT", result)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})

    assert response is not None
    last_text = "\n".join(b.text for b in response.content if b.type == "text")
    return (
        f"Stopped after {MAX_ITERS} iterations without a final answer.\n\n"
        f"Last response: {last_text}"
    )


def _call_key(name: str, args: dict) -> str:
    """Stable hash for a (tool_name, args) pair."""
    try:
        canonical = json.dumps(args, sort_keys=True)
    except (TypeError, ValueError):
        canonical = str(args)
    digest = hashlib.sha256(f"{name}:{canonical}".encode()).hexdigest()[:16]
    return f"{name}:{digest}"
