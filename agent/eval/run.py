"""
CLI entry point for the eval harness (Layer 1 + Layer 2).

    python -m agent.eval.run [OPTIONS]

Options:
    --questions  generated | curated | all   (default: all)
    --n-runs     N                           (default: EVAL_N_RUNS from config)
    --model      MODEL_ID                    (default: EVAL_MODEL from config)
    --output     PATH                        (default: stdout only, no file)
    --quiet                                  (suppress per-run progress)

For Layer 3 (LLM-as-judge reasoning quality) see:
    python -m agent.eval.judge.run --layer3        # run L1+L2+L3 on curated questions
    python -m agent.eval.judge.run --validate-gold # score the judge against the gold set
"""
from __future__ import annotations

import argparse
import json
import sys

from agent.config import EVAL_MODEL, EVAL_N_RUNS
from agent.eval.harness import run_eval
from agent.eval.questions import ALL_QUESTIONS, CURATED_QUESTIONS, GENERATED_QUESTIONS


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="FrontierView agent eval harness (Layer 1 + 2)")
    p.add_argument(
        "--questions",
        choices=["generated", "curated", "all"],
        default="all",
        help="Which question set to evaluate (default: all)",
    )
    p.add_argument(
        "--n-runs",
        type=int,
        default=EVAL_N_RUNS,
        metavar="N",
        help=f"Runs per question (default: {EVAL_N_RUNS})",
    )
    p.add_argument(
        "--model",
        default=EVAL_MODEL,
        metavar="MODEL_ID",
        help=f"Model to evaluate (default: {EVAL_MODEL})",
    )
    p.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="Write JSON results to this file (default: stdout summary only)",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-question / per-run progress",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    questions = {
        "generated": GENERATED_QUESTIONS,
        "curated": CURATED_QUESTIONS,
        "all": ALL_QUESTIONS,
    }[args.questions]

    print(
        f"Running eval: {len(questions)} questions × {args.n_runs} runs "
        f"on {args.model}",
        file=sys.stderr,
    )

    results = run_eval(
        questions=questions,
        n_runs=args.n_runs,
        model=args.model,
        verbose=not args.quiet,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print(f"\nResults written to {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
