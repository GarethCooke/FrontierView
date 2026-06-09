# CC Brief — Agent Phase 4a: Adversarial-Review Remediation

**Branch:** continue on `feature/agent-phase4a` (now rebased onto `master` @ `3da0c2c`; diff is the clean 6-file streaming spine). Do not merge until F1 + F2 are applied, the new/changed tests pass, and the agent + FV suites stay green.

**Context:** Opus adversarial pass on the rebased 4a diff. The spine is sound — emit guards correct, `None`-path returns unchanged, `tool_result` streams summary-only (no detail store), SSE worker guarantees a sentinel in `finally`. Three findings below; F1 blocks merge, F2 should land before 4c builds a client, F3 optional.

Severity: **F1** = blocker (a test that lies), **F2** = should-fix (contract consistency), **F3** = minor.

---

## F1 — `test_byte_identical_no_sink_constructs_nothing` is vacuous (BLOCKER)

The test's docstring says it will "wrap RecordingSink constructor to detect instantiation," but the body only runs `run("test", event_sink=None)` and asserts the result is a `str`. It would pass even if the `None` path constructed and emitted a full event stream. The dangling comment is the tell that the intended assertion was dropped.

**Fix — make it assert what its name claims.** Verify that with `event_sink=None`, *no event object is constructed*. `loop.run` references the event classes as `_events.<Name>` (i.e. `agent.events.<Name>`) and resolves them at call time, so patching them in `agent.events` is visible to the loop.

Sketch (adapt to house style):

```python
def test_byte_identical_no_sink_constructs_nothing():
    import agent.events as ev
    from contextlib import ExitStack

    names = ["RunStarted", "AssistantText", "ToolCall", "ToolResultEvent",
             "CompactionEvent", "ErrorEvent", "FinalAnswer", "RunFinished"]
    built: list[str] = []

    def _spy(name):
        orig = getattr(ev, name)
        def _wrap(*a, **k):
            built.append(name)
            return orig(*a, **k)
        return _wrap

    # None path: assert nothing is constructed
    with ExitStack() as stack:
        for n in names:
            stack.enter_context(patch.object(ev, n, side_effect=_spy(n)))  # use MagicMock(side_effect=...)
        with patch("agent.loop.llm.call", side_effect=_two_shot()):
            run("test", event_sink=None)
    assert built == [], f"None path constructed events: {built}"

    # Positive control: with a sink, the SAME spy must fire (proves the tripwire works)
    built.clear()
    with ExitStack() as stack:
        for n in names:
            stack.enter_context(patch.object(ev, n, side_effect=_spy(n)))
        with patch("agent.loop.llm.call", side_effect=_two_shot()):
            run("test", event_sink=RecordingSink())
    assert built, "Spy never fired even with a sink — the test cannot detect construction"
```

The positive control is the important half: without it, a spy that silently never fires would also make `built == []` pass for the wrong reason. (If maintaining the spy isn't worth it, deleting the test is acceptable — `test_byte_identical_result_and_faithful_events` already covers result-level byte-identity — but do not leave a test whose body contradicts its name.)

---

## F2 — Inconsistent terminal marker on the escaped-exception path (SHOULD-FIX)

Every *internal* exit of `run()` (final, max-iters, truncation cap, duplicate-abort) emits a terminal `RunFinished`. But when `run()` *raises* and `agent_routes._worker` catches it, the worker emits `ErrorEvent(kind="loop")` then the sentinel — **no `RunFinished`**. A 4c client keying on `run_finished` as end-of-run never sees one on a hard failure.

**Fix — make the terminal uniform: every stream ends with `run_finished`.**

1. In `agent/events.py`, make `RunFinished.turns` optional so the worker can emit it without a turn count it doesn't have:
   ```python
   class RunFinished(_Base):
       type: Literal["run_finished"] = "run_finished"
       turns: int | None = None
       usage: dict[str, Any] | None = None
   ```
2. In `_worker`, on the exception path, emit `ErrorEvent(kind="loop")` **then** a `RunFinished(turns=None)` before the sentinel, sequencing off `sink.last_seq`:
   ```python
   except Exception as exc:
       base = sink.last_seq
       queue.put_nowait(ErrorEvent(seq=base + 1, t=time.time(), kind="loop", message=str(exc)))
       queue.put_nowait(RunFinished(seq=base + 2, t=time.time(), turns=None))
   finally:
       queue.put_nowait(_SENTINEL)
   ```
3. Update the transport-contract docstring at the top of `agent_routes.py`: the stream always ends with `run_finished`, preceded by `final_answer` (normal) or `error(kind="loop")` (hard failure); `turns=None` signals an aborted run.

(Alternative if you'd rather not touch the schema: leave the worker as-is and document the terminal as "`run_finished` OR `error(kind=loop)`, always followed by stream close," and have 4c treat all three as terminal. The uniform-`run_finished` fix above is cleaner for the client and is the default.)

---

## F3 — AC2 over-claims "same tool-call sequence" (MINOR, optional)

`test_byte_identical_result_and_faithful_events` asserts result-string equality and inspects the *sink* run's events, but never cross-checks the `None` run's tool sequence — and identical mocked responses pin the path either way, so a sink-induced divergence couldn't be caught.

**Fix (pick one):** either soften the docstring to "same answer; sink events faithfully project the sink run," or capture the `None` run via the existing `_eval_capture` and assert its `tool_call` sequence equals the sink run's `ToolCall` sequence.

---

## Tests to add / update

- F1: the rewritten test above (None path constructs nothing + positive control).
- F2: a worker-error test asserting the stream's **last** event is `run_finished` (not just that an `error` appears) — extend `test_error_path_terminal_event_and_closed_stream`. Add a `RunFinished(seq=…, t=…, turns=None)` sample to `test_schema_round_trip`.
- Re-run the existing byte-identical/result test — must stay green (F2 changes only the failure path).

## Acceptance criteria

- [ ] F1: `test_byte_identical_no_sink_constructs_nothing` asserts zero event construction on the `None` path, with a positive control proving the spy fires; or the test is removed. No test body contradicts its name.
- [ ] F2: every SSE stream terminates with `run_finished`; worker error path emits `error(loop)` → `run_finished(turns=None)` → sentinel; contract docstring updated; `RunFinished.turns` optional.
- [ ] F3 (optional): docstring softened or `None`-run sequence cross-checked.
- [ ] Agent + FV suites green on the branch.

## Do not regress

- The `None`-path byte-identity (return values + transcript) — F1/F2 touch only the sink/error paths.
- `tool_result` events stream the summary only, never the detail store.
- The off-by-default `AGENT_PUBLIC_ENABLED` gate.

## Build report

Confirm with: agent + FV suite counts, and one real `curl -N` frame dump against a known question (every endpoint test mocks `llm.call`, so nothing has exercised live SSE framing yet). I'll read the diff directly — generate and upload `git diff master..feature/agent-phase4a > phase4a.diff` — not a prose summary.
