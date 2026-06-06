# CC Brief — FrontierView Agent, Phase 1: The Spine

**Project:** FrontierView agent (an LLM agent that drives the existing Almgren market-impact model via tool calls)
**Phase:** 1 of 4 — the spine
**Lives in:** the FrontierView repo, as a new `agent/` package
**Deliverable:** a hand-rolled agent loop, callable from a CLI, that answers a natural-language question by calling two model tools in-process and printing a visible trace.

---

## 1. Goal

A working reason → act → observe loop. From the command line, the user types a question; the agent plans, calls one or both of two FrontierView tools, observes the results, and returns an answer. Every step is printed so the loop is legible.

This phase deliberately builds *only* the core mechanism. It is the thing the rest of the project hangs off, so it must be correct and clean, not feature-rich.

## 2. Hard constraints (do not violate)

- **No model logic in `agent/`.** The tools are thin wrappers that import and call FrontierView's existing model functions. No formula, parameter, or computation is reimplemented or copied into the agent package. This is enforced by a test (see §8).
- **Hand-rolled loop.** No agent framework (no LangChain, LlamaIndex, etc.). The loop is plain Python so its mechanics are explicit.
- **Do not break the existing FV API.** If any refactor is needed (§5), the existing FastAPI service and its tests must still pass unchanged in behaviour.
- **No secrets in the repo.** The LLM API key comes from an environment variable.

## 3. Scope

**In scope (Phase 1):**
- Optional shared-core extraction so the model functions are importable (§5).
- The `agent/` package skeleton (§4).
- Exactly two tools, wrapping existing FV functions (§6).
- The agent loop with a stop condition and an iteration guard (§7).
- A read-only tool dispatcher with an allowlist.
- A step tracer that prints reasoning, each tool call + args, and each result.
- A CLI entrypoint.
- Tool errors fed back to the model as observations (no crashes).

**Explicitly out of scope (later phases — do not build now):**
- The `/agent` FastAPI endpoint and the web UI tab (Phase 4).
- Response streaming (Phase 4).
- The eval harness (Phase 3).
- The full tool set, prompt caching, rate limiting, public deployment (Phases 2–4).
- Multi-provider abstraction beyond the single isolation point in §9.

## 4. Target structure

Create `agent/` alongside the existing packages. Final shape (existing dirs shown for orientation):

```
frontierview/
  core/            # shared model logic — may already exist; see §5
  api/             # existing FastAPI service (imports core)
  agent/
    __init__.py
    llm.py         # thin client around ONE hosted provider's tool-use API
    tools.py       # tool schemas + read-only dispatcher (imports core)
    loop.py        # the agent loop
    trace.py       # step printer
    cli.py         # entrypoint: python -m agent.cli "question"
    config.py      # model name, max iterations, env loading
    docs/
      briefs/      # this brief lives here
      frontierview_agent_cost_model.xlsx
    tests/
      test_tools.py
      test_loop.py
```

## 5. The shared-core check (DRY-critical, do this first)

Before writing tools, confirm the model functions for cost/variance and the optimal schedule are importable as plain functions.

- **If they already live in an importable module** (e.g. `core/`), the agent imports them as-is. Nothing to refactor.
- **If the computation currently lives inside FastAPI route handlers**, lift it into a plain module (`core/impact.py` or similar) that *both* the API routes and the agent tools import. The existing `parameters.py` single-source-of-truth stays exactly as is and is read through, not duplicated. Update the API routes to call the extracted functions. Verify existing FV tests still pass.

Do not proceed to §6 until the model functions can be imported without going through HTTP and without copying any logic.

## 6. The two tools

Wrap the **actual existing** FrontierView functions. Do not invent signatures — bind each tool to the real function and map the tool's parameters onto the real ones. Both tools are pure, read-only computations.

1. **`cost_and_variance`** — given an execution schedule (and optionally model parameters), return the expected cost and variance. Wraps FV's existing cost/variance computation.
2. **`optimal_schedule`** — given a risk-aversion level (and optional constraints), return the optimal execution schedule, and its cost and variance. Wraps FV's existing optimal/frontier computation.

For each tool define:
- a name, a one-line description, and a typed parameter schema (in the provider's tool-format) with honest types and required/optional flags matching the real function;
- a dispatch function that validates inputs, calls the core function, and returns a small JSON-serialisable result (numbers, not prose).

Keep each wrapper to delegation only — no maths in the wrapper.

## 7. The loop

Implement in `loop.py`, roughly:

```
messages = [ user_question ]
for i in range(MAX_ITERS):
    response = llm.call(system_prompt, tool_schemas, messages)
    trace(response.reasoning_text)

    if response.has_tool_calls:
        append assistant turn to messages
        for call in response.tool_calls:
            trace(call.name, call.args)
            result = dispatcher.run(call.name, call.args)   # allowlist + try/except
            trace(result)
            append tool_result to messages
        continue            # observe, then loop

    else:                   # final text answer
        trace(response.text)
        return response.text

# loop guard hit:
return "Stopped after MAX_ITERS steps without a final answer." + last state
```

Rules:
- **Stop condition:** the model returns a text answer with no tool calls.
- **Iteration guard:** `MAX_ITERS` (default 8) hard cap; on hit, return gracefully with the trace, never loop forever.
- **Dispatcher:** an explicit allowlist of the two tool names. An unknown name returns an error result fed back to the model, not an exception.
- **Error recovery:** wrap each tool call in try/except; on failure append an error `tool_result` (`{"error": "..."}`) so the model can react, rather than crashing the run.

## 8. Tests

- `test_tools.py`: assert each tool's output **equals a direct call to the underlying core function** for the same inputs. This is the DRY guarantee — if logic were duplicated or drifted, this fails.
- `test_tools.py`: unknown-tool dispatch returns an error result, not a raised exception.
- `test_loop.py`: with the LLM client stubbed to emit a scripted tool call then a final answer, the loop dispatches the tool, appends the result, and terminates on the final answer. (No live API calls in tests.)
- The existing FV test suite still passes (regression check on the §5 refactor).

## 9. Config & provider

- Single hosted provider with native tool use for Phase 1. **Default: Anthropic Claude (Haiku 4.5)** — cheap, tool-reliable, and in your existing ecosystem. The provider call is isolated entirely inside `llm.py`, so swapping to OpenAI or Gemini later is a one-file change. (For zero-cost development, Google AI Studio's free Flash tier is the alternative — same isolation point.)
- `config.py`: model name, `MAX_ITERS`, and API-key loading from env (`ANTHROPIC_API_KEY` or equivalent). Fail with a clear message if the key is missing.
- Add the provider SDK to the project dependencies.

## 10. Acceptance criteria

Phase 1 is done when:
1. `python -m agent.cli "..."` runs end to end and prints: the model's reasoning, each tool call with arguments, each tool result, and a final answer.
2. A question needing both tools (e.g. *"What's the optimal schedule at moderate risk aversion, and what are its expected cost and variance?"*) produces a sensible answer via real tool calls.
3. All tests in §8 pass, including the DRY equality test and the existing FV suite.
4. The iteration guard demonstrably stops a runaway loop; a forced tool error is recovered, not fatal.
5. No model logic exists in `agent/`; no secrets are committed.

## 11. Design rationale (for the record)

- In-process tools (not HTTP calls to FV's own API) because it keeps the loop the focus and avoids a network hop; the shared-core extraction is what makes this DRY rather than duplicative.
- One concrete provider, not an abstraction layer, because the point of Phase 1 is to learn one real tool-use protocol end to end; the isolation in `llm.py` keeps a second provider cheap to add later without obscuring the loop now.
- Two tools, not five, because the loop mechanics are identical at two and five — the extra tools are Phase 2 breadth, not Phase 1 learning.
