# CC Brief — FrontierView Agent, Phase 2 compaction test hardening

**Branch:** rides the existing `feature/agent-phase2` (Phase 2 not yet merged — this is part of the same PR).
**File:** `agent/tests/test_compaction.py` — single test only.
**Scope:** test-only change. Do **not** touch compaction logic unless §4 forces it.

---

## 1. Problem

`test_compaction_fires_in_loop_and_no_already_run_tool_re_called` is named for the property "established facts preserved, no re-call," but its assertions don't reach it:

```python
assert isinstance(answer, str)
assert answer  # non-empty
assert call_log.count("cost_and_variance")
```

These are trivially true or mock-bound. The mock LLM is scripted (4 tool rounds, then a final answer regardless of transcript state), so `call_log` reflects the script, not the compaction logic. Nothing inspects the `messages` handed to the final LLM call, so nothing verifies the cost fact established before compaction actually **survived** into the model's final prompt. The fact-preservation mechanism is unit-tested in isolation (`test_compact_summary_mentions_tool_facts`), but the loop-level guarantee is untested.

## 2. Goal

Make the integration test assert that, after compaction fires inside the loop, the established tool fact is still present in the running-state summary the model receives on its final turn.

## 3. The change

**Pre-edit check.** Read `agent/config.py` for `COMPACTION_KEEP_RECENT_TURNS` and the loop's max-iteration cap. The mock currently runs 4 tool rounds (`if n <= 4:`). If keep-recent ≥ 4, four rounds leaves almost nothing to fold into the summary, making the new fact assertion vacuous. Raise the mock's round count to a few above `COMPACTION_KEEP_RECENT_TURNS` so real history gets folded — but keep it **strictly below** the loop's max-iteration cap, or the loop guard will abort before the final answer and the test fails for the wrong reason. Pick the number from the actual config values; do not hardcode blindly.

**Edit 1 — capture list,** next to `call_log`:

```python
    captured_final_messages: list = []
```

**Edit 2 — capture the final prompt,** as the first line of the `# Final call: return answer` branch:

```python
        captured_final_messages.append(list(messages))
```

**Edit 3 — replace the three tail assertions** with:

```python
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
```

The `final_prompt[1]` index follows the `compact_messages()` contract already relied on by `test_compact_preserves_original_question` (q at [0]) and `test_compact_summary_mentions_tool_facts` (summary at [1]). If the loop's post-compaction layout differs, adjust the index to locate the assistant summary — do not weaken the assertion to a whole-prompt substring search, which the verbatim recent turns would satisfy trivially.

## 4. If the fact-survival assert fails

The test is the spec. If it fails because the fact genuinely isn't in the summary (not an index mismatch), that is a **real compaction bug** — the running state isn't carrying established facts forward. Fix the compaction logic so it does, then the test passes. Do not relax the test to make it green.

## 5. Acceptance

- `python -m pytest agent/tests/test_compaction.py -k re_called -v` passes and genuinely exercises folding (round count > keep-recent, under the iteration cap).
- Full agent suite + FV regression suite green on `feature/agent-phase2`.
- No changes outside this test, except a compaction-logic fix if §4 applies.
- DRY equality test still green.

## 6. Out of scope

Other tests; compaction config defaults; any non-test code except a genuine §4 fix.
