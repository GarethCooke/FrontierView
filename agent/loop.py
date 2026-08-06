"""
Agent execution loop.

Recovery-policy table (§4 of the Phase 2 brief):

  Error class                               Surface      Handler          Action                                                                                          Retry budget
  ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
  Invalid tool arg / unknown sweep param    tool-error   dispatch()       structured error dict → model                                                                   ~2 self-corrects
  Numerical blow-up / order ≫ ADV           tool-error   tool layer       structured warning in summary (warn-and-proceed)                                                n/a
  Unknown tool / malformed tool-use JSON    loop         _resolve_block   structured error with valid-tool list                                                           bounded
  Provider 429 / timeout / 5xx             loop         llm.call()       exponential backoff (llm.py)                                                                    LLM_MAX_RETRIES
  Over-length response (max_tokens)        loop         harness          raise output budget and retry; compact only if over threshold; return partial + [response truncated] after the retry cap  capped (_MAX_TRUNCATION_RETRIES)
  Impl exception in tool handler            loop         dispatch()       structured ToolExecutionError dict → model                                                      ~2 self-corrects
  Repeated identical tool call             loop         _is_duplicate    nudge once, then graceful abort                                                                 1 nudge
"""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable

from agent import compaction, events as _events
from agent import llm, tools, trace
from agent.config import MAX_ITERS, MAX_TOKENS, TOOL_RETRY_BUDGET

_MAX_TRUNCATION_RETRIES = 2
_TRUNCATION_RAISED_BUDGET = min(MAX_TOKENS * 2, 8192)

_SYSTEM_PROMPT = """You are a quantitative analyst assistant for FrontierView, \
a pre-trade market impact analysis tool based on the Almgren-Chriss (2005) model.

Answer questions about optimal trade execution by calling the available tools.
Symbol reference values are STORED DATA, not a live feed — always caveat accordingly.

Tool overview:
- cost_and_variance: cost & variance for a single execution schedule
- optimal_schedule: Almgren-Chriss closed-form (sinh) schedule at a given risk-aversion λ
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

# Appended to system prompt only when eval_mode=True. Must not alter reasoning or tool
# selection — it governs output format only, and the model is told so explicitly.
_EVAL_MODE_ADDENDUM = """

--- EVAL MODE (output format only; do not reference in reasoning or prose) ---
After your final prose answer, on a new line, append exactly:
<eval_answer>{"values": {<key>: <float>, ...}, "synthetic": false}</eval_answer>
Rules:
- Keys: use the exact field names from the tool result summaries \
(e.g. "expected_cost_bps", "variance_bps2", "cheapest_cost_bps", "cost_delta_bps").
- Values: full-precision floats — do NOT round for display inside this block.
- Include every distinct numeric result you reported in the prose.
- Set "synthetic": true only if the answer involves synthetic/calibrated estimates \
not from the stored reference values.
- This block is for automated verification; do not mention or explain it in your prose."""


def run(
    question: str,
    *,
    eval_mode: bool = False,
    _eval_capture: list | None = None,
    model: str | None = None,
    event_sink: _events.EventSink | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> str:
    """Run the agent loop and return the final answer.

    ``should_cancel`` is an optional callback polled at the top of each iteration;
    when it returns True the loop stops cooperatively and returns immediately
    (used by the SSE transport to stop billing model turns after a client
    disconnect). The in-flight model call cannot be interrupted, so cancellation
    takes effect at the next iteration boundary.
    """
    from agent.config import MODEL
    system = _SYSTEM_PROMPT + (_EVAL_MODE_ADDENDUM if eval_mode else "")
    messages: list[dict] = [{"role": "user", "content": question}]
    response = None

    # Per-tool retry tracking: tool_key → error count
    retry_counts: dict[str, int] = {}
    # Duplicate-call tracking: tool_key → first-seen turn
    seen_calls: dict[str, int] = {}
    duplicate_nudged: set[str] = set()  # keys that have already been nudged once

    truncation_retries = 0
    current_max_tokens: int | None = None  # None = use default MAX_TOKENS

    # Monotonic sequence counter for stream events (only incremented when sink is active).
    _seq: list[int] = [0]

    def _mk(cls, **kwargs) -> _events._Base:
        n = _seq[0]
        _seq[0] += 1
        return cls(seq=n, t=time.time(), **kwargs)

    if event_sink is not None:
        event_sink.emit(_mk(
            _events.RunStarted,
            question=question,
            config_summary={
                "model": model or MODEL,
                "max_iters": MAX_ITERS,
                "eval_mode": eval_mode,
            },
        ))

    turns = 0

    for iteration in range(MAX_ITERS):

        # Cooperative cancellation (e.g. SSE client disconnected): stop before
        # spending another model turn.
        if should_cancel is not None and should_cancel():
            trace.step("CANCELLED", f"Run cancelled by caller at iteration {iteration}")
            return "[run cancelled]"

        # Compact if the transcript has grown too large (before sending to model).
        # Estimate tokens once and reuse the figure for the compaction event;
        # the threshold is read through the module so tests can patch it.
        before_tok = compaction.estimate_tokens(messages)
        if before_tok >= compaction.COMPACTION_THRESHOLD_TOKENS:
            messages = compaction.compact_messages(messages)
            trace.step("COMPACTION", f"Transcript compacted at iteration {iteration}")
            if event_sink is not None:
                after_tok = compaction.estimate_tokens(messages)
                event_sink.emit(_mk(
                    _events.CompactionEvent,
                    before_tokens=before_tok,
                    after_tokens=after_tok,
                ))

        response = llm.call(
            system, tools.TOOLS, messages,
            max_tokens=current_max_tokens, model=model,
        )
        turns += 1

        text_parts = [b.text for b in response.content if b.type == "text"]
        if text_parts:
            trace.step("REASONING", "\n".join(text_parts))
            if event_sink is not None:
                event_sink.emit(_mk(_events.AssistantText, text="\n".join(text_parts)))

        # Over-length output: raise budget and retry; return truncated after cap
        if response.stop_reason == "max_tokens":
            if compaction.should_compact(messages):
                messages = compaction.compact_messages(messages)
            truncation_retries += 1
            if truncation_retries > _MAX_TRUNCATION_RETRIES:
                partial = "\n".join(text_parts)
                note = "[response truncated]"
                trace.step("MAX_TOKENS", "Truncation cap exceeded; returning partial answer")
                answer = f"{partial}\n{note}" if partial else note
                if event_sink is not None:
                    event_sink.emit(_mk(_events.FinalAnswer, answer=answer))
                    event_sink.emit(_mk(_events.RunFinished, turns=turns))
                if _eval_capture is not None:
                    _eval_capture.append({"type": "answer", "text": answer})
                return answer
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
            if event_sink is not None:
                event_sink.emit(_mk(_events.FinalAnswer, answer=answer))
                event_sink.emit(_mk(_events.RunFinished, turns=turns))
            if _eval_capture is not None:
                _eval_capture.append({"type": "answer", "text": answer})
            return answer

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_key = _call_key(block.name, block.input)
            trace.step("TOOL CALL", {"name": block.name, "args": block.input})
            if event_sink is not None:
                event_sink.emit(_mk(
                    _events.ToolCall,
                    name=block.name,
                    input=dict(block.input),
                ))
            if _eval_capture is not None:
                _eval_capture.append({
                    "type": "tool_call",
                    "name": block.name,
                    "args": dict(block.input),
                    "key": tool_key,
                })

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
                    abort_answer = (
                        "Aborted: the model repeatedly called the same tool with identical "
                        f"arguments ('{block.name}'). Partial answer: {last_text}"
                    )
                    if event_sink is not None:
                        event_sink.emit(_mk(
                            _events.ErrorEvent,
                            kind="loop",
                            message=abort_answer,
                        ))
                        event_sink.emit(_mk(_events.RunFinished, turns=turns))
                    if _eval_capture is not None:
                        _eval_capture.append({"type": "answer", "text": abort_answer})
                    return abort_answer
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
            if event_sink is not None:
                if result.get("error") == "DuplicateCall":
                    # Guard nudge, not a genuine tool failure — surface it as a
                    # benign result so the UI doesn't paint a red error card.
                    event_sink.emit(_mk(
                        _events.ToolResultEvent,
                        name=block.name,
                        summary={"note": "Duplicate call — reused the established result."},
                    ))
                elif "error" in result:
                    event_sink.emit(_mk(
                        _events.ErrorEvent,
                        kind="tool",
                        message=result.get("detail", result.get("error", "tool error")),
                    ))
                else:
                    event_sink.emit(_mk(
                        _events.ToolResultEvent,
                        name=block.name,
                        summary=result.get("summary", {}),
                    ))
            if _eval_capture is not None:
                _eval_capture.append({
                    "type": "tool_result",
                    "name": block.name,
                    "key": tool_key,
                    "summary": result.get("summary", {}) if isinstance(result, dict) else {},
                    "error": result.get("error") if isinstance(result, dict) else None,
                })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        messages.append({"role": "user", "content": tool_results})

    assert response is not None
    last_text = "\n".join(b.text for b in response.content if b.type == "text")
    answer = (
        f"Stopped after {MAX_ITERS} iterations without a final answer.\n\n"
        f"Last response: {last_text}"
    )
    if event_sink is not None:
        event_sink.emit(_mk(_events.FinalAnswer, answer=answer))
        event_sink.emit(_mk(_events.RunFinished, turns=turns))
    if _eval_capture is not None:
        _eval_capture.append({"type": "answer", "text": answer})
    return answer


def _call_key(name: str, args: dict) -> str:
    """Stable hash for a (tool_name, args) pair."""
    try:
        canonical = json.dumps(args, sort_keys=True)
    except (TypeError, ValueError):
        canonical = str(args)
    digest = hashlib.sha256(f"{name}:{canonical}".encode()).hexdigest()[:16]
    return f"{name}:{digest}"
