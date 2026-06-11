# CC Brief — Agent Phase 4b Fast-Follows (M1, M2, N1–N3)

Branch: `feature/agent-phase4b-followups` (off `master`, post-4b merge). Small, surgical diff — no behaviour changes to the loop, limiter logic, or stream contract beyond what's specified. Baseline 146 tests green before and after; one new test added (147 expected).

## F1 (= review M1 + M2, fixed together) — Budget classification moves behind the provider seam

The 4b implementation imports `anthropic` directly in `api/agent_routes.py` and checks `__cause__` one level deep. Two problems: a provider swap is now a two-file change (violating the `llm.py` isolation point), and any future re-wrap of the exception chain silently degrades the budget terminal back to `kind="loop"`. Fix both by moving detection inside `llm.py`:

1. **`agent/llm.py`:** define

   ```python
   class ProviderBudgetError(Exception):
       """Raised when retries are exhausted on a 429-class provider error
       (spend cap or sustained throttling). Provider-agnostic signal;
       detection of the provider-specific exception stays in this module."""
   ```

   In the existing retry-exhaustion path: if the final error is `anthropic.RateLimitError`, raise `ProviderBudgetError(<existing wrapper message>) from last_error` instead of the generic `RuntimeError`. All other exhaustion paths raise exactly what they raise today — do not touch the generic message string (other tests may assert on it).

2. **`api/agent_routes.py`:** delete the `import anthropic` line. `_is_budget_exhausted` becomes a `__cause__`-chain walk for the provider-agnostic type:

   ```python
   from agent.llm import ProviderBudgetError

   def _is_budget_exhausted(exc: BaseException) -> bool:
       seen: set[int] = set()
       cur: BaseException | None = exc
       while cur is not None and id(cur) not in seen:
           if isinstance(cur, ProviderBudgetError):
               return True
           seen.add(id(cur))
           cur = cur.__cause__
       return False
   ```

   (The `seen` guard is cheap cycle insurance; chains shouldn't cycle, but a `while` over attacker-shaped... no — over *future-refactor*-shaped data shouldn't be able to hang the worker.)

3. **`llm.py` docstring:** add one line noting it is the sole provider seam *including* budget-error classification — a provider swap must map the new provider's 429-class error to `ProviderBudgetError` here and touch nothing else.

### Test reshaping for F1

The current T8 helper (`_make_rate_limit_cause`) hand-constructs a wrapper that *mirrors* llm.py's output — a mirror that can drift. Replace with two tests split at the seam:

- **New llm-seam test** (in the existing llm test module): stub the underlying client call to raise `anthropic.RateLimitError` persistently; assert retry exhaustion raises `ProviderBudgetError` with the original error chained as `__cause__`. Positive control: a persistent non-429 error still raises the existing generic exhaustion exception.
- **T8 rewritten:** patch `agent.loop.llm.call` to raise `ProviderBudgetError("…")` directly (no hand-built mirror); assert the stream terminates `error(kind="budget", <locked message>) → run_finished(turns=None)`. Keep the existing positive control (plain `RuntimeError` → `kind="loop"`). Delete `_make_rate_limit_cause` and T8's `anthropic`/`httpx` imports.

Net: the budget path is now covered end-to-end with no test-side replica of llm.py internals.

## F2 (= review N1) — Schema round-trip sample renumber

`agent/tests/test_streaming.py::test_schema_round_trip`: the 4b addition created a duplicate `seq=7` (`ErrorEvent(seq=7, …, kind="budget", …)` collides with `FinalAnswer(seq=7, …)`). Renumber the sample list so `seq` is strictly increasing across all entries. No assertion changes.

## F3 (= review N2) — Day-window docstring honesty

`api/rate_limiter.py` module docstring (and the `RateLimiter` class docstring if it repeats it): state explicitly that both windows are **anchored at first request** — the day window is a rolling 24h from the IP's first counted request, **not** UTC-calendar-aligned. One sentence; prevents a future "why didn't it reset at midnight" debugging ghost.

## F4 (= review N3) — Loop-bound literals in the routes docstring

`api/agent_routes.py` docstring currently claims `MAX_ITERS=8, MAX_TOKENS=4096` as the existing loop bounds. The diff never proved those values.

1. Grep `agent/loop.py` (and wherever the constants actually live) for the real names and values.
2. Replace the literals in the docstring with a **pointer, not numbers**: e.g. "the existing loop bounds (`agent.loop.MAX_ITERS` / `MAX_TOKENS` — see agent/loop.py)". Literals in a comment rot; a pointer can't. If the constant *names* in the docstring are wrong too, correct them to the real names.

## Acceptance criteria

- AC1: `api/agent_routes.py` contains no `anthropic` import; budget classification is `isinstance(..., ProviderBudgetError)` over the full `__cause__` chain.
- AC2: `llm.py` raises `ProviderBudgetError` (original chained) on 429-class retry exhaustion, generic path byte-identical otherwise; docstring names llm.py as the sole provider seam including budget classification.
- AC3: llm-seam test + reshaped T8 as specified; `_make_rate_limit_cause` deleted; no test constructs a replica of llm.py's wrapper.
- AC4: round-trip sample seq strictly increasing; rate-limiter docstring states anchored (non-calendar) windows; routes docstring references loop-bound constants by name/pointer with no literal values.
- AC5: 147 tests green (146 baseline + 1 llm-seam test; T8 reshaped in place).
