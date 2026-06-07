"""
Eval harness: Layer 1 (answer correctness) + Layer 2 (trace-based checks).

Usage:
    from agent.eval.harness import run_eval
    from agent.eval.questions import ALL_QUESTIONS

    results = run_eval(ALL_QUESTIONS, n_runs=20)

The harness never calls Layer 3 (LLM-as-judge) — that is a separate brief.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field

from agent import loop
from agent.config import EVAL_MODEL, EVAL_N_RUNS
from agent.eval import layer2, scorer
from agent.eval.questions.types import Question


# ---------------------------------------------------------------------------
# Per-run and per-question result shapes
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    run_index: int
    answer: str
    parsed: dict | None           # parsed eval_answer block, or None
    actual_values: dict[str, float]
    value_scores: dict[str, bool]  # {gt_key: within_tolerance}
    layer1_passed: bool            # True if all gt_values scored correctly (or no gt_values)
    layer2: dict                   # output of layer2.run_all()
    parse_failed: bool             # True if eval_answer block was absent/malformed
    capture: list[dict]            # raw capture events (tool_calls, tool_results, answer)
    elapsed_s: float


@dataclass
class QuestionResult:
    question: Question
    runs: list[RunResult] = field(default_factory=list)

    @property
    def n(self) -> int:
        return len(self.runs)

    @property
    def k_layer1(self) -> int:
        """Runs where Layer 1 passed."""
        return sum(1 for r in self.runs if r.layer1_passed)

    @property
    def success_rate(self) -> float:
        return self.k_layer1 / self.n if self.n else 0.0

    @property
    def parse_failure_rate(self) -> float:
        return sum(1 for r in self.runs if r.parse_failed) / self.n if self.n else 0.0

    @property
    def layer2_failure_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {
            "tool_path": 0,
            "no_excess_duplicates": 0,
            "synthetic_caveat": 0,
            "numeric_groundedness": 0,
        }
        for r in self.runs:
            for k in counts:
                if not r.layer2.get(k, {}).get("passed", True):
                    counts[k] += 1
        return counts


# ---------------------------------------------------------------------------
# Single-run execution
# ---------------------------------------------------------------------------


def _run_once(question: Question, run_index: int, model: str) -> RunResult:
    capture: list[dict] = []
    t0 = time.monotonic()
    answer = loop.run(
        question.text,
        eval_mode=True,
        _eval_capture=capture,
        model=model,
    )
    elapsed = time.monotonic() - t0

    parsed = scorer.parse_eval_answer(answer)
    parse_failed = parsed is None and bool(question.gt_values)

    actual_values = scorer.extract_values(parsed)
    value_scores = scorer.score_values(
        actual_values, question.gt_values, question.tolerance_overrides
    )

    # Layer 1 passes if:
    #   - no gt_values defined (qualitative / out-of-tool question) → pass by default
    #   - all gt_values were scored within tolerance
    #   - parse failure with gt_values → automatic fail (counted above in parse_failed)
    if not question.gt_values:
        layer1_passed = True
    elif parse_failed:
        layer1_passed = False
    else:
        layer1_passed = scorer.all_passed(value_scores)

    l2 = layer2.run_all(capture, question)

    return RunResult(
        run_index=run_index,
        answer=answer,
        parsed=parsed,
        actual_values=actual_values,
        value_scores=value_scores,
        layer1_passed=layer1_passed,
        layer2=l2,
        parse_failed=parse_failed,
        capture=capture,
        elapsed_s=elapsed,
    )


# ---------------------------------------------------------------------------
# Question-level runner
# ---------------------------------------------------------------------------


def run_question(
    question: Question,
    n_runs: int = EVAL_N_RUNS,
    model: str = EVAL_MODEL,
    verbose: bool = False,
) -> QuestionResult:
    result = QuestionResult(question=question)
    for i in range(n_runs):
        if verbose:
            print(f"  run {i + 1}/{n_runs}...", end=" ", flush=True)
        run = _run_once(question, i, model)
        result.runs.append(run)
        if verbose:
            status = "pass" if run.layer1_passed else "FAIL"
            print(f"{status} ({run.elapsed_s:.1f}s)")
    return result


# ---------------------------------------------------------------------------
# Full eval runner
# ---------------------------------------------------------------------------


def run_eval(
    questions: list[Question] | None = None,
    n_runs: int = EVAL_N_RUNS,
    model: str = EVAL_MODEL,
    verbose: bool = True,
) -> dict:
    """Run the full eval harness and return a structured results dict.

    Args:
        questions: list of Question objects; defaults to ALL_QUESTIONS.
        n_runs: runs per question (default: EVAL_N_RUNS from config).
        model: model ID to use (default: EVAL_MODEL from config).
        verbose: print per-question progress to stdout.

    Returns a dict suitable for JSON serialisation.
    """
    if questions is None:
        from agent.eval.questions import ALL_QUESTIONS
        questions = ALL_QUESTIONS

    question_results: list[QuestionResult] = []

    for idx, q in enumerate(questions):
        if verbose:
            print(
                f"\n[{idx + 1}/{len(questions)}] {q.id} "
                f"({'out-of-tool' if q.out_of_tool else 'numeric' if q.gt_values else 'qualitative'})"
            )
            print(f"  Q: {q.text[:80]}{'...' if len(q.text) > 80 else ''}")
        qr = run_question(q, n_runs=n_runs, model=model, verbose=verbose)
        question_results.append(qr)
        if verbose:
            _print_question_summary(qr)

    results = _build_results_dict(question_results, model, n_runs)
    if verbose:
        _print_aggregate(results)
    return results


# ---------------------------------------------------------------------------
# Results formatting
# ---------------------------------------------------------------------------


def _print_question_summary(qr: QuestionResult) -> None:
    l2_fails = qr.layer2_failure_counts
    l2_str = ", ".join(f"{k}:{v}" for k, v in l2_fails.items() if v > 0)
    print(
        f"  Layer1: {qr.k_layer1}/{qr.n} "
        f"(success={qr.success_rate:.0%}, parse_fail={qr.parse_failure_rate:.0%})"
        + (f"  Layer2 fails: {l2_str}" if l2_str else "")
    )


def _print_aggregate(results: dict) -> None:
    agg = results["aggregate"]
    print("\n" + "=" * 60)
    print("AGGREGATE")
    print(f"  Questions:          {results['n_questions']}")
    print(f"  Runs per question:  {results['n_runs_per_question']}")
    print(f"  Model:              {results['model']}")
    print(f"  Overall L1 success: {agg['overall_success_rate']:.1%}")
    print(f"  Parse failure rate: {agg['parse_failure_rate']:.1%}")
    print("  Layer 2 failure rates:")
    for k, v in agg["layer2_failure_rates"].items():
        print(f"    {k}: {v:.1%}")
    print("=" * 60)


def _build_results_dict(
    question_results: list[QuestionResult],
    model: str,
    n_runs: int,
) -> dict:
    questions_data = []
    for qr in question_results:
        runs_data = []
        for r in qr.runs:
            runs_data.append({
                "run_index": r.run_index,
                "layer1_passed": r.layer1_passed,
                "parse_failed": r.parse_failed,
                "actual_values": r.actual_values,
                "value_scores": r.value_scores,
                "layer2_passed": r.layer2["passed"],
                "layer2_detail": {
                    k: v for k, v in r.layer2.items() if k != "passed"
                },
                "elapsed_s": round(r.elapsed_s, 2),
            })

        l2_counts = qr.layer2_failure_counts
        questions_data.append({
            "id": qr.question.id,
            "text": qr.question.text,
            "source": qr.question.source,
            "out_of_tool": qr.question.out_of_tool,
            "expected_tool_path": qr.question.expected_tool_path,
            "gt_values": qr.question.gt_values,
            "success_rate": round(qr.success_rate, 4),
            "k": qr.k_layer1,
            "n": qr.n,
            "parse_failure_rate": round(qr.parse_failure_rate, 4),
            "layer2_failure_counts": l2_counts,
            "runs": runs_data,
        })

    # Aggregate statistics
    numeric_questions = [qr for qr in question_results if qr.question.gt_values]
    all_rates = [qr.success_rate for qr in numeric_questions] if numeric_questions else [1.0]
    all_parse_rates = [qr.parse_failure_rate for qr in numeric_questions] if numeric_questions else [0.0]
    overall_success = sum(all_rates) / len(all_rates)
    overall_parse_fail = sum(all_parse_rates) / len(all_parse_rates)

    l2_keys = ["tool_path", "no_excess_duplicates", "synthetic_caveat", "numeric_groundedness"]
    l2_fail_rates: dict[str, float] = {}
    for k in l2_keys:
        total_runs = sum(qr.n for qr in question_results)
        total_fails = sum(qr.layer2_failure_counts.get(k, 0) for qr in question_results)
        l2_fail_rates[k] = total_fails / total_runs if total_runs else 0.0

    return {
        "model": model,
        "n_questions": len(question_results),
        "n_runs_per_question": n_runs,
        "questions": questions_data,
        "aggregate": {
            "overall_success_rate": round(overall_success, 4),
            "parse_failure_rate": round(overall_parse_fail, 4),
            "layer2_failure_rates": {k: round(v, 4) for k, v in l2_fail_rates.items()},
        },
    }
