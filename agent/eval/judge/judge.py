"""
Layer 3 judge: LLM-as-judge scoring of reasoning quality.

A stronger model (default Sonnet) scores each agent trace on the five rubric
dimensions defined in rubric.py.  The judge sees:
  - the question text
  - the full trace (tool calls + model-visible tool results + final prose answer)
  - known-correct behaviour annotations from the Question object
  - the rubric with anchors and exemplars

The judge does NOT see:
  - the eval_answer block (stripped before judging)
  - Layer 1 / Layer 2 pass/fail outcomes (judge is blind to numeric correctness)

Output (JSON only, no prose preamble):
  {"scores": {"<dim>": {"score": "pass|partial|fail", "evidence": "<trace span>"}, ...}}
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field

import anthropic

from agent.config import (
    JUDGE_BUDGET_CALLS,
    JUDGE_MODEL,
    JUDGE_SAMPLE_M,
    JUDGE_TEMPERATURE,
    LLM_MAX_RETRIES,
    api_key,
)
from agent.eval.judge.rubric import DIMENSIONS, RUBRIC
from agent.eval.questions.types import Question

_EVAL_BLOCK_RE = re.compile(r"<eval_answer>.*?</eval_answer>", re.DOTALL)
_BACKOFF_BASE = 1.0

_client: anthropic.Anthropic | None = None
_client_lock = threading.Lock()


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        # Double-checked locking: judge runs may race on first use.
        with _client_lock:
            if _client is None:
                _client = anthropic.Anthropic(api_key=api_key())
    return _client


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class DimensionScore:
    score: str    # "pass" | "partial" | "fail" | "na"
    evidence: str


@dataclass
class JudgeResult:
    scores: dict[str, DimensionScore] = field(default_factory=dict)
    raw_response: str = ""
    parse_error: str | None = None

    def score_for(self, dim: str) -> str | None:
        ds = self.scores.get(dim)
        return ds.score if ds else None


# ---------------------------------------------------------------------------
# Trace formatting
# ---------------------------------------------------------------------------


def strip_eval_answer(text: str) -> str:
    return _EVAL_BLOCK_RE.sub("", text).strip()


def format_trace(capture: list[dict]) -> str:
    lines: list[str] = []
    for event in capture:
        t = event.get("type", "?")
        if t == "tool_call":
            args_str = json.dumps(event.get("args", {}))
            lines.append(
                f"[TOOL_CALL] {event.get('name', '?')} "
                f"(key={event.get('key', '')}) args={args_str}"
            )
        elif t == "tool_result":
            err = event.get("error")
            if err:
                lines.append(
                    f"[TOOL_RESULT] {event.get('name', '?')} "
                    f"(key={event.get('key', '')}) ERROR: {err}"
                )
            else:
                summary_str = json.dumps(event.get("summary", {}))
                lines.append(
                    f"[TOOL_RESULT] {event.get('name', '?')} "
                    f"(key={event.get('key', '')}) summary={summary_str}"
                )
        elif t == "answer":
            text = strip_eval_answer(event.get("text", ""))
            lines.append(f"[ANSWER]\n{text}")
        else:
            lines.append(f"[{t.upper()}] {json.dumps(event)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _build_rubric_section() -> str:
    parts: list[str] = []
    for dim in DIMENSIONS:
        r = RUBRIC[dim]
        parts.append(f"### Dimension: {dim}")
        parts.append(r["description"])
        parts.append("Anchors:")
        for score_label, anchor_text in r["anchors"].items():
            parts.append(f"  {score_label}: {anchor_text}")
        parts.append("Exemplars:")
        for ex in r["exemplars"]:
            parts.append(f"  Trace span: {ex['trace_span']}")
            parts.append(f"  Score: {ex['score']} — {ex['reason']}")
        if "domain_anchors" in r:
            parts.append("Domain anchor checklist (check each; score ONLY against these):")
            for da in r["domain_anchors"]:
                parts.append(f"  [{da['id']}] Rule: {da['rule']}")
                parts.append(f"    Pass example: {da['pass_example']}")
                parts.append(f"    Fail example: {da['fail_example']}")
        parts.append("")
    return "\n".join(parts)


_RUBRIC_SECTION = _build_rubric_section()  # built once at import time

_OUTPUT_SCHEMA = json.dumps(
    {
        "scores": {
            dim: {"score": "pass|partial|fail", "evidence": "<verbatim trace quote>"}
            for dim in DIMENSIONS
        }
    },
    indent=2,
)


def build_judge_prompt(question: Question, capture: list[dict]) -> str:
    trace_text = format_trace(capture)

    known_lines: list[str] = []
    if question.expected_tool_path:
        known_lines.append(f"Minimal expected tool path: {question.expected_tool_path}")
    if question.out_of_tool:
        known_lines.append(
            "This is an out-of-tool request. Correct behaviour: decline or caveat substantively."
        )
    if question.synthetic:
        known_lines.append(
            "A synthetic-recovery result is involved. Caveat required in the answer."
        )
    if question.notes:
        known_lines.append(f"Annotator notes: {question.notes}")
    known_text = "\n".join(known_lines) if known_lines else "No additional annotations."

    return f"""You are a reasoning-quality judge for a quantitative finance agent.

Score the agent trace below on five dimensions. Return ONLY valid JSON — no preamble, \
no explanation, no markdown fences. Use this exact schema:

{_OUTPUT_SCHEMA}

## Scoring instructions

- Score each dimension INDEPENDENTLY; do not let one bias another.
- You are BLIND to Layer 1/2 outcomes — do not infer or reference whether numeric values were correct.
- For each dimension, quote a short verbatim span from the trace as "evidence" (under 80 words).
- For assumption_handling: if the question is fully specified, score "pass".
- For out_of_tool_handling: if this is a normal in-tool question, score "pass".
- For domain_correctness: score ONLY against the anchored exemplars in the rubric — \
do not apply independent quant finance knowledge. If the answer makes no claim touching \
any of the four domain anchors (citation, permanent_linear, ac_optimality, \
synthetic_recovery), score "pass" by default.

## Rubric

{_RUBRIC_SECTION}

## Question

{question.text}

## Known-correct behaviour

{known_text}

## Full trace (eval_answer block has been removed)

{trace_text}
"""


# ---------------------------------------------------------------------------
# LLM call + parsing
# ---------------------------------------------------------------------------


def _call_judge_raw(prompt: str, judge_model: str, temperature: float) -> str:
    client = _get_client()
    last_exc: Exception | None = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            resp = client.messages.create(
                model=judge_model,
                max_tokens=1024,
                temperature=temperature,
                system=[{
                    "type": "text",
                    "text": "You are a strict JSON-only judge. Output only valid JSON.",
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text  # type: ignore[index,union-attr]
        except anthropic.RateLimitError as exc:
            last_exc = exc
        except anthropic.APIStatusError as exc:
            if exc.status_code < 500:
                raise
            last_exc = exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            last_exc = exc
        if attempt < LLM_MAX_RETRIES - 1:
            time.sleep(_BACKOFF_BASE * (2 ** attempt))
    raise RuntimeError(
        f"Judge call failed after {LLM_MAX_RETRIES} retries: {last_exc}"
    ) from last_exc


def parse_judge_response(raw: str) -> tuple[dict[str, DimensionScore], str | None]:
    text = raw.strip()
    # Strip markdown fences if the model added them despite instructions
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, f"JSON parse error: {exc}"

    raw_scores = data.get("scores", {})
    if not isinstance(raw_scores, dict):
        return {}, "Missing or non-dict 'scores' key"

    scores: dict[str, DimensionScore] = {}
    for dim in DIMENSIONS:
        dim_data = raw_scores.get(dim, {})
        score_val = str(dim_data.get("score", "")).lower()
        if score_val not in {"pass", "partial", "fail", "na"}:
            score_val = "fail"
        evidence = str(dim_data.get("evidence", ""))
        scores[dim] = DimensionScore(score=score_val, evidence=evidence)

    return scores, None


def call_judge(
    question: Question,
    capture: list[dict],
    judge_model: str = JUDGE_MODEL,
    temperature: float = JUDGE_TEMPERATURE,
) -> JudgeResult:
    prompt = build_judge_prompt(question, capture)
    raw = _call_judge_raw(prompt, judge_model, temperature)
    scores, error = parse_judge_response(raw)
    return JudgeResult(scores=scores, raw_response=raw, parse_error=error)


# ---------------------------------------------------------------------------
# Batch runner (over harness QuestionResults)
# ---------------------------------------------------------------------------


def run_layer3(
    question_results: list,      # list[harness.QuestionResult]
    judge_model: str = JUDGE_MODEL,
    temperature: float = JUDGE_TEMPERATURE,
    sample_m: int | None = None,    # None → JUDGE_SAMPLE_M from config
    budget_calls: int | None = None,  # None → JUDGE_BUDGET_CALLS from config
    verbose: bool = False,
) -> dict:
    """Score a sample of traces from harness QuestionResult objects.

    Returns a dict keyed by question id with per-run judge scores and a
    per-question dimension summary.

    Cost note: one judge call ≈ 1-2k input tokens + ~200 output tokens at
    Sonnet rates (~$0.003/call at 2025 pricing).  With default sample_m=3
    on ~20 curated questions that is ~60 calls ≈ $0.18 per full Layer 3 run.
    The budget_calls cap prevents runaway cost.
    """
    if sample_m is None:
        sample_m = JUDGE_SAMPLE_M
    if budget_calls is None:
        budget_calls = JUDGE_BUDGET_CALLS

    calls_made = 0
    layer3: dict = {}

    for qr in question_results:
        q = qr.question
        runs_to_judge = qr.runs[:sample_m]
        run_scores: list[dict] = []

        for run in runs_to_judge:
            if calls_made >= budget_calls:
                if verbose:
                    print(f"  [Layer3] budget cap ({budget_calls}) reached; stopping.")
                break
            if not run.capture:
                run_scores.append({"run_index": run.run_index, "skipped": "no capture"})
                continue

            jr = call_judge(q, run.capture, judge_model, temperature)
            calls_made += 1

            if verbose:
                status = "ok" if not jr.parse_error else f"parse_err"
                scores_str = " ".join(
                    f"{d}={jr.scores[d].score}" for d in DIMENSIONS if d in jr.scores
                ) if not jr.parse_error else jr.parse_error or ""
                print(f"  [Layer3] {q.id} run{run.run_index}: {status} | {scores_str}")

            run_scores.append({
                "run_index": run.run_index,
                "parse_error": jr.parse_error,
                "scores": {
                    dim: {"score": jr.scores[dim].score, "evidence": jr.scores[dim].evidence}
                    for dim in DIMENSIONS
                    if dim in jr.scores
                } if not jr.parse_error else {},
            })

        # Aggregate: modal score per dimension across judged runs
        dim_summary: dict[str, dict] = {}
        for dim in DIMENSIONS:
            scored = [
                r["scores"][dim]["score"]
                for r in run_scores
                if "scores" in r and dim in r["scores"]
            ]
            if scored:
                modal = max(set(scored), key=scored.count)
                dim_summary[dim] = {
                    "modal_score": modal,
                    "distribution": {
                        s: scored.count(s) for s in ("pass", "partial", "fail")
                    },
                }

        layer3[q.id] = {
            "question_source": q.source,
            "runs_judged": len(run_scores),
            "dim_summary": dim_summary,
            "runs": run_scores,
        }

    return {
        "judge_model": judge_model,
        "sample_m": sample_m,
        "calls_made": calls_made,
        "budget_calls": budget_calls,
        "questions": layer3,
    }
