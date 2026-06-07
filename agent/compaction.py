"""
Thresholded transcript compaction.

When the estimated token count of the running messages list exceeds
COMPACTION_THRESHOLD_TOKENS, the loop calls compact_messages() to:

  1. Keep messages[0] (the original user question) verbatim.
  2. Replace the "middle" turns with a short running-state summary that
     preserves every established tool-call fact.
  3. Keep the most recent COMPACTION_KEEP_RECENT_TURNS×2 messages verbatim.

The summary is injected as a synthetic (assistant, user) pair so the message
sequence remains valid (alternating roles).  The assistant turn summarises
what was found; the user turn says "Continue."

The summary also records which tools have already run with which arguments,
so the model does not redundantly re-call them after compaction.
"""
from __future__ import annotations

import json

from agent.config import COMPACTION_KEEP_RECENT_TURNS, COMPACTION_THRESHOLD_TOKENS

# Token estimate: ~4 chars/token (English text heuristic).
# JSON overhead means this underestimates by ~25-30% — the actual trigger fires
# later than COMPACTION_THRESHOLD_TOKENS implies. Treat the config as approximate.
_CHARS_PER_TOKEN = 4


def estimate_tokens(messages: list[dict]) -> int:
    try:
        return len(json.dumps(messages)) // _CHARS_PER_TOKEN
    except (TypeError, ValueError):
        # Fallback for non-JSON-serializable content (e.g. Anthropic SDK objects)
        return len(str(messages)) // _CHARS_PER_TOKEN


def should_compact(messages: list[dict]) -> bool:
    return estimate_tokens(messages) >= COMPACTION_THRESHOLD_TOKENS


def compact_messages(
    messages: list[dict],
    keep_recent: int = COMPACTION_KEEP_RECENT_TURNS,
) -> list[dict]:
    """Replace middle turns with a running-state summary.

    Returns a new list; the input is not mutated.
    Idempotent if there is nothing to compact.
    """
    verbatim_count = keep_recent * 2  # each keep = 1 assistant + 1 user turn

    if len(messages) <= verbatim_count + 1:
        return messages  # nothing to compact

    original_question = messages[0]

    # Split at a turn boundary (assistant turn = odd index)
    split = len(messages) - verbatim_count
    # Ensure recent block starts on an assistant turn (odd index)
    if split % 2 == 0:
        split += 1  # nudge forward one message to land on an assistant turn

    middle = messages[1:split]
    recent = messages[split:]

    facts = _extract_facts(middle)
    summary_text = _build_summary_text(facts)

    return [
        original_question,
        {
            "role": "assistant",
            "content": [{"type": "text", "text": summary_text}],
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "Continue the analysis with this context."}],
        },
        *recent,
    ]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_facts(messages: list[dict]) -> list[dict]:
    """Scan middle turns for tool-use/tool-result pairs and extract key facts."""
    facts: list[dict] = []
    # Collect tool_use blocks (in assistant turns) and pair with their results
    pending: dict[str, dict] = {}  # tool_use_id → {name, args}

    for msg in messages:
        if msg["role"] == "assistant":
            content = msg["content"]
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        pending[block["id"]] = {"name": block["name"], "args": block["input"]}
                    elif getattr(block, "type", None) == "tool_use":
                        pending[getattr(block, "id")] = {"name": getattr(block, "name"), "args": getattr(block, "input")}

        elif msg["role"] == "user":
            content = msg["content"]
            if isinstance(content, list):
                for block in content:
                    block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
                    if block_type == "tool_result":
                        tool_use_id = block.get("tool_use_id") if isinstance(block, dict) else block.tool_use_id
                        raw_content = block.get("content") if isinstance(block, dict) else block.content

                        if tool_use_id in pending:
                            meta = pending.pop(tool_use_id)
                            result = _parse_tool_content(raw_content)
                            facts.append({
                                "tool": meta["name"],
                                "args": meta["args"],
                                "result": result,
                            })

    return facts


def _parse_tool_content(content) -> dict:
    """Parse raw tool result content into a dict."""
    if isinstance(content, str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return {"raw": content}
    if isinstance(content, dict):
        return content
    if isinstance(content, list):
        # Anthropic content block list
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        combined = " ".join(texts)
        try:
            return json.loads(combined)
        except (json.JSONDecodeError, TypeError):
            return {"raw": combined}
    return {}


def _format_fact(fact: dict) -> str:
    """Format a single tool fact into a human-readable line.

    Dumps the full summary so no fields (cost_delta_bps, caveat, etc.) are lost.
    """
    tool = fact["tool"]
    args = fact["args"]
    result = fact.get("result", {})
    summary = result.get("summary", result)

    symbol = args.get("symbol", "?")
    order = args.get("order_size", "?")
    horizon = args.get("horizon_hours", "?")
    stype = args.get("schedule_type") or args.get("schedule", "")
    lam = args.get("lambda_risk")

    key_args = f"{symbol}, order={order}, horizon={horizon}h"
    if stype:
        key_args += f", schedule={stype}"
    if lam is not None:
        key_args += f", λ={lam:.2e}"

    return f"- {tool}({key_args}): {json.dumps(summary, default=str)}"


def _build_summary_text(facts: list[dict]) -> str:
    if not facts:
        return "[Compacted history — no tool results to summarise.]"

    lines = [
        f"[Compacted history — {len(facts)} prior tool call(s) summarised below.]",
        "Established facts (do NOT re-call these tools with the same arguments):",
    ]
    lines.extend(_format_fact(f) for f in facts)
    return "\n".join(lines)
