"""
CLI entry point for Layer 3 (LLM-as-judge reasoning quality).

    python -m agent.eval.judge.run [OPTIONS]

Modes:
  --layer3          Run Layer 1+2 first (curated questions only), then judge a
                    sample of the captured traces.  Prints per-question dimension
                    summary and an integrated L1+L2+L3 aggregate.
  --validate-gold   Run the judge against the hand-labelled gold set and report
                    per-dimension agreement + self-consistency.  Does NOT run the
                    agent; use this to audit the judge itself.

Common options:
  --judge-model MODEL_ID    Judge model (default: JUDGE_MODEL from config)
  --sample-m N              Traces per question to judge (default: JUDGE_SAMPLE_M)
  --budget N                Max total judge API calls (default: JUDGE_BUDGET_CALLS)
  --consistency-runs N      Runs for self-consistency check (default: 3; validate only)
  --output PATH             Write JSON result to file (default: stdout summary only)
  --quiet                   Suppress per-question progress

Cost note (Sonnet 2025 pricing, ~1-2k input + ~200 output tokens per call):
  default sample_m=3 × ~20 curated questions  →  ~60 judge calls  ≈ $0.18
  full gold validation (25 items × 1 run)     →  ~25 judge calls  ≈ $0.08
  gold self-consistency (25 items × 3 runs)   →  ~75 judge calls  ≈ $0.23
"""
from __future__ import annotations

import argparse
import json
import sys

from agent.config import (
    EVAL_MODEL,
    EVAL_N_RUNS,
    JUDGE_BUDGET_CALLS,
    JUDGE_MODEL,
    JUDGE_SAMPLE_M,
    JUDGE_TEMPERATURE,
)
from agent.eval.judge.judge import run_layer3
from agent.eval.judge.validation import run_validation


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="FrontierView eval — Layer 3 (LLM-as-judge reasoning quality)"
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--layer3",
        action="store_true",
        help="Run Layer 1+2 on curated questions then judge a trace sample",
    )
    mode.add_argument(
        "--validate-gold",
        action="store_true",
        help="Score the judge against the hand-labelled gold set",
    )
    p.add_argument(
        "--judge-model",
        default=JUDGE_MODEL,
        metavar="MODEL_ID",
        help=f"Judge model (default: {JUDGE_MODEL})",
    )
    p.add_argument(
        "--sample-m",
        type=int,
        default=JUDGE_SAMPLE_M,
        metavar="M",
        help=f"Traces per question to judge in --layer3 mode (default: {JUDGE_SAMPLE_M})",
    )
    p.add_argument(
        "--budget",
        type=int,
        default=JUDGE_BUDGET_CALLS,
        metavar="N",
        help=f"Max judge API calls (default: {JUDGE_BUDGET_CALLS})",
    )
    p.add_argument(
        "--consistency-runs",
        type=int,
        default=3,
        metavar="N",
        help="Self-consistency runs per gold item in --validate-gold mode (default: 3)",
    )
    p.add_argument(
        "--agent-model",
        default=EVAL_MODEL,
        metavar="MODEL_ID",
        help=f"Agent model for --layer3 mode (default: {EVAL_MODEL})",
    )
    p.add_argument(
        "--n-runs",
        type=int,
        default=EVAL_N_RUNS,
        metavar="N",
        help=f"Agent runs per question in --layer3 mode (default: {EVAL_N_RUNS})",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Write JSON result to file (default: stdout summary only)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-question progress",
    )
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# --layer3 mode: run harness on curated questions then judge traces
# ---------------------------------------------------------------------------


def _run_layer3_mode(args: argparse.Namespace) -> dict:
    from agent.eval.harness import run_question
    from agent.eval.questions import CURATED_QUESTIONS

    verbose = not args.quiet
    if verbose:
        print(
            f"Layer 3 run: {len(CURATED_QUESTIONS)} curated questions × {args.n_runs} runs "
            f"on {args.agent_model}, then judging {args.sample_m} traces/question "
            f"with {args.judge_model}",
            file=sys.stderr,
        )

    question_results = []
    for idx, q in enumerate(CURATED_QUESTIONS):
        if verbose:
            print(
                f"\n[{idx + 1}/{len(CURATED_QUESTIONS)}] {q.id}",
                file=sys.stderr,
            )
        qr = run_question(q, n_runs=args.n_runs, model=args.agent_model, verbose=verbose)
        question_results.append(qr)

    layer3_data = run_layer3(
        question_results,
        judge_model=args.judge_model,
        temperature=JUDGE_TEMPERATURE,
        sample_m=args.sample_m,
        budget_calls=args.budget,
        verbose=verbose,
    )

    # Build integrated summary
    from agent.eval.harness import _build_results_dict
    l12_data = _build_results_dict(question_results, args.agent_model, args.n_runs)

    result = {
        **l12_data,
        "layer3": layer3_data,
        "layer3_aggregate": _build_layer3_aggregate(layer3_data),
    }

    if verbose:
        _print_layer3_summary(result)

    return result


def _build_layer3_aggregate(layer3_data: dict) -> dict:
    from agent.eval.judge.rubric import DIMENSIONS

    dim_scores: dict[str, list[str]] = {d: [] for d in DIMENSIONS}
    for q_data in layer3_data["questions"].values():
        for dim, summary in q_data.get("dim_summary", {}).items():
            dim_scores[dim].append(summary["modal_score"])

    def _rate(scores: list[str], value: str) -> float:
        return scores.count(value) / len(scores) if scores else 0.0

    return {
        dim: {
            "pass_rate": round(_rate(dim_scores[dim], "pass"), 4),
            "partial_rate": round(_rate(dim_scores[dim], "partial"), 4),
            "fail_rate": round(_rate(dim_scores[dim], "fail"), 4),
            "n_questions": len(dim_scores[dim]),
        }
        for dim in DIMENSIONS
    }


def _print_layer3_summary(result: dict) -> None:
    from agent.eval.judge.rubric import DIMENSIONS

    agg = result.get("aggregate", {})
    l3_agg = result.get("layer3_aggregate", {})
    l3 = result.get("layer3", {})

    print("\n" + "=" * 60, file=sys.stderr)
    print("LAYER 1+2 AGGREGATE", file=sys.stderr)
    print(f"  Overall L1 success: {agg.get('overall_success_rate', 0):.1%}", file=sys.stderr)
    print(f"  Parse failure rate: {agg.get('parse_failure_rate', 0):.1%}", file=sys.stderr)
    print("  L2 failure rates:", file=sys.stderr)
    for k, v in agg.get("layer2_failure_rates", {}).items():
        print(f"    {k}: {v:.1%}", file=sys.stderr)

    print("\nLAYER 3 AGGREGATE", file=sys.stderr)
    print(
        f"  Judge: {l3.get('judge_model')}  "
        f"calls: {l3.get('calls_made')}/{l3.get('budget_calls')}",
        file=sys.stderr,
    )
    for dim in DIMENSIONS:
        d = l3_agg.get(dim, {})
        print(
            f"  {dim}: pass={d.get('pass_rate', 0):.0%} "
            f"partial={d.get('partial_rate', 0):.0%} "
            f"fail={d.get('fail_rate', 0):.0%}",
            file=sys.stderr,
        )
    print("=" * 60, file=sys.stderr)


# ---------------------------------------------------------------------------
# --validate-gold mode
# ---------------------------------------------------------------------------


def _run_validate_gold_mode(args: argparse.Namespace) -> dict:
    return run_validation(
        judge_model=args.judge_model,
        temperature=JUDGE_TEMPERATURE,
        n_consistency_runs=args.consistency_runs,
        verbose=not args.quiet,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.layer3:
        result = _run_layer3_mode(args)
    else:
        result = _run_validate_gold_mode(args)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2)
        print(f"\nResults written to {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
