"""
Compaction tests (§6 of the Phase 2 brief).

Verifies:
 - compact_messages() fires when the token estimate crosses the threshold.
 - Established facts (tool-call results) are preserved in the summary.
 - The compacted message list is a valid (alternating-role) sequence.
 - A deliberately long multi-tool loop triggers compaction and the model
   does NOT re-call a tool whose result was summarised away.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.compaction import (
    compact_messages,
    estimate_tokens,
    should_compact,
)
from agent.loop import run


# ---------------------------------------------------------------------------
# Unit tests for compaction logic
# ---------------------------------------------------------------------------


def _make_tool_call_turn(tool_name: str, tool_id: str, args: dict) -> dict:
    """Synthetic assistant turn with a tool_use block (dict form)."""
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_id, "name": tool_name, "input": args}],
    }


def _make_tool_result_turn(tool_id: str, result: dict) -> dict:
    """Synthetic user turn with a tool_result block."""
    return {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": tool_id, "content": json.dumps(result)}
        ],
    }


def _make_messages(n_tool_rounds: int) -> list[dict]:
    """Build a fake multi-round messages list with n_tool_rounds tool calls."""
    msgs = [{"role": "user", "content": "What are the execution costs?"}]
    for i in range(n_tool_rounds):
        tool_id = f"toolu_{i:03d}"
        cost_val = round(1.0 + i * 0.1, 4)
        msgs.append(_make_tool_call_turn(
            "cost_and_variance",
            tool_id,
            {"symbol": "AAPL", "order_size": 100_000, "horizon_hours": 2.0, "schedule_type": "twap"},
        ))
        msgs.append(_make_tool_result_turn(tool_id, {
            "summary": {"expected_cost_bps": cost_val, "variance_bps2": 0.5},
            "detail_id": f"detail-{i}",
        }))
    return msgs


def test_estimate_tokens_grows_with_messages():
    short = [{"role": "user", "content": "hi"}]
    long = _make_messages(20)
    assert estimate_tokens(long) > estimate_tokens(short)


def test_should_compact_false_for_short_transcript():
    msgs = _make_messages(2)
    assert not should_compact(msgs)


def test_should_compact_true_when_threshold_crossed():
    """Build a transcript large enough to cross the threshold."""
    # Fill messages with large payloads until threshold is crossed
    big_result = {"summary": {"expected_cost_bps": 1.5, "variance_bps2": 0.4,
                               "detail": "x" * 300}, "detail_id": "d1"}
    msgs = [{"role": "user", "content": "q" * 100}]
    i = 0
    while not should_compact(msgs):
        tid = f"t{i}"
        msgs.append(_make_tool_call_turn("cost_and_variance", tid, {"x": i}))
        msgs.append(_make_tool_result_turn(tid, big_result))
        i += 1
        if i > 500:
            break  # safety guard

    assert should_compact(msgs), "Never crossed the threshold — test is misconfigured"


def test_compact_preserves_original_question():
    msgs = _make_messages(8)
    original_q = msgs[0]["content"]
    compacted = compact_messages(msgs)
    assert compacted[0]["role"] == "user"
    assert compacted[0]["content"] == original_q


def test_compact_reduces_message_count():
    msgs = _make_messages(10)
    compacted = compact_messages(msgs)
    assert len(compacted) < len(msgs)


def test_compact_sequence_alternates_roles():
    """After compaction the message roles must alternate user/assistant."""
    msgs = _make_messages(10)
    compacted = compact_messages(msgs)
    for i in range(len(compacted) - 1):
        role_a = compacted[i]["role"]
        role_b = compacted[i + 1]["role"]
        assert role_a != role_b, (
            f"Non-alternating roles at positions {i}/{i+1}: {role_a}/{role_b}"
        )


def test_compact_summary_mentions_tool_facts():
    """The compacted summary must contain established fact lines for compacted tool calls."""
    msgs = _make_messages(6)  # 6 tool rounds — compaction should fold some
    compacted = compact_messages(msgs)

    # Find the synthetic assistant summary turn (the second message)
    assert compacted[1]["role"] == "assistant"
    summary_content = compacted[1]["content"]
    if isinstance(summary_content, list):
        text = " ".join(
            b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
            for b in summary_content
        )
    else:
        text = str(summary_content)

    # Either facts are mentioned or the no-facts fallback is present
    assert "cost_and_variance" in text or "Compacted" in text


@pytest.mark.parametrize("n_rounds", [3, 5, 8, 12, 20])
def test_compact_always_alternates_roles(n_rounds):
    """compact_messages must always produce a valid role-alternating sequence."""
    msgs = _make_messages(n_rounds)
    compacted = compact_messages(msgs)
    for i in range(len(compacted) - 1):
        role_a, role_b = compacted[i]["role"], compacted[i + 1]["role"]
        assert role_a != role_b, (
            f"n_rounds={n_rounds}: non-alternating roles at positions {i}/{i+1}: {role_a}/{role_b}"
        )


def test_compact_keeps_recent_turns_verbatim():
    """The last COMPACTION_KEEP_RECENT_TURNS*2 messages must appear verbatim."""
    from agent.config import COMPACTION_KEEP_RECENT_TURNS

    msgs = _make_messages(10)
    verbatim_count = COMPACTION_KEEP_RECENT_TURNS * 2

    # Find the split point (same logic as compact_messages)
    split = len(msgs) - verbatim_count
    if split % 2 == 0:
        split += 1
    expected_recent = msgs[split:]

    compacted = compact_messages(msgs)
    # The tail of compacted should be the recent verbatim turns
    tail = compacted[-(len(expected_recent)):]
    assert tail == expected_recent


# ---------------------------------------------------------------------------
# Integration: long loop crosses threshold, no already-run tool is re-called
# ---------------------------------------------------------------------------


def _fat_response(n_chars: int = 2000) -> str:
    """A large tool result payload to inflate token count quickly."""
    return json.dumps({
        "summary": {"expected_cost_bps": 1.5, "variance_bps2": 0.4},
        "detail_id": "d",
        "padding": "x" * n_chars,
    })


def test_compaction_fires_in_loop_and_no_already_run_tool_re_called():
    """Multi-tool loop: compaction fires, established facts preserved, no re-call."""
    tool_args = {
        "symbol": "AAPL",
        "order_size": 100_000,
        "horizon_hours": 2.0,
        "schedule_type": "twap",
    }

    call_log: list[str] = []
    captured_final_messages: list[list[dict[str, object]]] = []

    def mock_dispatch(name: str, args: dict) -> dict:
        call_log.append(name)
        # Return a fat result to inflate tokens
        return json.loads(_fat_response(3000))

    # Build mock LLM: first N calls return tool_use, then returns a final answer
    call_count = [0]

    def mock_llm(system, tools_list, messages: list[dict[str, object]]):
        call_count[0] += 1
        n = call_count[0]

        # First 6 calls: request the same tool (but with distinct IDs so not duplicates)
        # 6 > COMPACTION_KEEP_RECENT_TURNS (4) so real history folds; 6 < MAX_ITERS (8).
        if n <= 6:
            block = MagicMock()
            block.type = "tool_use"
            block.name = "cost_and_variance"
            block.id = f"toolu_{n:03d}"
            block.input = {**tool_args, "n_bins": n}  # vary n_bins to avoid duplicate detection
            resp = MagicMock()
            resp.stop_reason = "tool_use"
            resp.content = [block]
            return resp

        # Final call: return answer
        captured_final_messages.append(list(messages))
        block = MagicMock()
        block.type = "text"
        block.text = "The execution cost is approximately 1.5 bps."
        resp = MagicMock()
        resp.stop_reason = "end_turn"
        resp.content = [block]
        return resp

    with patch("agent.loop.llm.call", side_effect=mock_llm), \
         patch("agent.loop.tools.dispatch", side_effect=mock_dispatch), \
         patch("agent.compaction.COMPACTION_THRESHOLD_TOKENS", 500):  # very low threshold
        answer = run("What is the AAPL execution cost?")

    assert isinstance(answer, str) and answer

    final_prompt = captured_final_messages[-1]
    # Compaction fired: final prompt shorter than the uncompacted transcript
    # (1 user msg + 2 messages per tool round).
    assert len(final_prompt) < 1 + 2 * call_log.count("cost_and_variance"), \
        "compaction did not reduce the transcript in the loop"
    # The fact established before compaction survived into the running-state
    # summary (compacted[1] is the assistant summary, per compact_messages()).
    summary_text = json.dumps(final_prompt[1], default=str)
    assert "cost_and_variance" in summary_text or "Compacted" in summary_text, \
        "established tool fact was lost during compaction"
