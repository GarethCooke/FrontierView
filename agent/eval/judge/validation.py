"""
Layer 3 judge validation: score the judge against the hand-labelled gold set.

Measures:
  - Judge-vs-human agreement per dimension (exact match rate).
  - Judge self-consistency: run the judge N times on the same gold traces and
    measure cross-run agreement per dimension.
  - Dimensions below JUDGE_AGREEMENT_THRESHOLD are flagged as "rubric needs work".
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agent.config import (
    JUDGE_AGREEMENT_THRESHOLD,
    JUDGE_MODEL,
    JUDGE_TEMPERATURE,
)
from agent.eval.judge.judge import JudgeResult, call_judge
from agent.eval.judge.rubric import DIMENSIONS
from agent.eval.questions.types import Question

_GOLD_PATH = Path(__file__).parent / "gold" / "gold_set.json"

VALID_SCORES = frozenset({"pass", "partial", "fail"})


# ---------------------------------------------------------------------------
# Gold item data shape
# ---------------------------------------------------------------------------


@dataclass
class GoldItem:
    id: str
    question: Question
    capture: list[dict]
    human_labels: dict[str, str]   # dim → "pass"|"partial"|"fail"|"na"
    human_notes: str = ""


def load_gold_set(path: Path = _GOLD_PATH) -> list[GoldItem]:
    raw: list[dict] = json.loads(path.read_text(encoding="utf-8"))
    items: list[GoldItem] = []
    for entry in raw:
        q = Question(
            id=entry["question_id"],
            text=entry["question_text"],
            expected_tool_path=entry.get("expected_tool_path", []),
            gt_values={},
            out_of_tool=entry.get("out_of_tool", False),
            synthetic=entry.get("synthetic", False),
            notes=entry.get("notes", ""),
            source="gold",
        )
        items.append(GoldItem(
            id=entry["id"],
            question=q,
            capture=entry["trace"],
            human_labels=entry["human_labels"],
            human_notes=entry.get("human_notes", ""),
        ))
    return items


# ---------------------------------------------------------------------------
# Agreement helpers
# ---------------------------------------------------------------------------


def _agreement_rate(judge_scores: list[str], human_scores: list[str]) -> float:
    if not judge_scores:
        return 0.0
    return sum(j == h for j, h in zip(judge_scores, human_scores)) / len(judge_scores)


def _per_dimension_agreement(
    pairs: list[tuple[GoldItem, JudgeResult]],
) -> dict[str, float]:
    """Exact-match agreement rate per dimension, ignoring na-labelled items."""
    dim_j: dict[str, list[str]] = {d: [] for d in DIMENSIONS}
    dim_h: dict[str, list[str]] = {d: [] for d in DIMENSIONS}

    for gold, jr in pairs:
        if jr.parse_error:
            continue
        for dim in DIMENSIONS:
            h = gold.human_labels.get(dim, "na")
            if h not in VALID_SCORES:
                continue
            j_score = jr.scores.get(dim)
            if j_score is None:
                continue
            dim_j[dim].append(j_score.score)
            dim_h[dim].append(h)

    return {dim: _agreement_rate(dim_j[dim], dim_h[dim]) for dim in DIMENSIONS}


# ---------------------------------------------------------------------------
# Self-consistency
# ---------------------------------------------------------------------------


def measure_self_consistency(
    gold_items: list[GoldItem],
    judge_model: str = JUDGE_MODEL,
    temperature: float = JUDGE_TEMPERATURE,
    n_runs: int = 3,
    verbose: bool = False,
) -> dict[str, float]:
    """Run the judge n_runs times on each gold trace; report cross-run agreement."""
    all_runs: list[list[JudgeResult]] = []
    for item in gold_items:
        if verbose:
            print(f"  [consistency] {item.id} × {n_runs} runs ...", end=" ", flush=True)
        runs = [
            call_judge(item.question, item.capture, judge_model, temperature)
            for _ in range(n_runs)
        ]
        all_runs.append(runs)
        if verbose:
            print("done")

    dim_agree: dict[str, list[float]] = {d: [] for d in DIMENSIONS}
    for runs in all_runs:
        ref = runs[0]
        if ref.parse_error:
            continue
        for other in runs[1:]:
            if other.parse_error:
                continue
            for dim in DIMENSIONS:
                r_s = ref.scores.get(dim)
                o_s = other.scores.get(dim)
                if r_s and o_s:
                    dim_agree[dim].append(float(r_s.score == o_s.score))

    return {
        dim: (sum(vals) / len(vals) if vals else 0.0)
        for dim, vals in dim_agree.items()
    }


# ---------------------------------------------------------------------------
# Main validation runner
# ---------------------------------------------------------------------------


def run_validation(
    judge_model: str = JUDGE_MODEL,
    temperature: float = JUDGE_TEMPERATURE,
    n_consistency_runs: int = 3,
    threshold: float = JUDGE_AGREEMENT_THRESHOLD,
    verbose: bool = True,
    gold_path: Path = _GOLD_PATH,
) -> dict:
    """Run the judge against the gold set and return a validation report dict."""
    gold_items = load_gold_set(gold_path)
    if verbose:
        print(f"Loaded {len(gold_items)} gold items from {gold_path}")
        print(f"Judge model: {judge_model}  temperature: {temperature}")

    pairs: list[tuple[GoldItem, JudgeResult]] = []
    for item in gold_items:
        jr = call_judge(item.question, item.capture, judge_model, temperature)
        pairs.append((item, jr))
        if verbose:
            if jr.parse_error:
                print(f"  {item.id}: PARSE_ERROR — {jr.parse_error}")
            else:
                scores_str = " ".join(
                    f"{d[:4]}={jr.scores[d].score}" for d in DIMENSIONS if d in jr.scores
                )
                print(f"  {item.id}: {scores_str}")

    agreement = _per_dimension_agreement(pairs)
    parse_failures = sum(1 for _, jr in pairs if jr.parse_error)

    if verbose:
        print(f"\nParse failures: {parse_failures}/{len(gold_items)}")
        print("\nPer-dimension judge-human agreement:")
        for dim, rate in agreement.items():
            flag = "  ⚠  BELOW THRESHOLD — rubric needs work" if rate < threshold else ""
            print(f"  {dim}: {rate:.1%}{flag}")

    if verbose:
        print(f"\nRunning self-consistency ({n_consistency_runs} runs per item) ...")
    consistency = measure_self_consistency(
        gold_items,
        judge_model=judge_model,
        temperature=temperature,
        n_runs=n_consistency_runs,
        verbose=verbose,
    )

    if verbose:
        print("\nJudge self-consistency (agreement across runs):")
        for dim, rate in consistency.items():
            print(f"  {dim}: {rate:.1%}")

    below_threshold = [dim for dim, rate in agreement.items() if rate < threshold]

    # Per-item detail for the JSON output
    items_detail = []
    for gold, jr in pairs:
        item_d: dict = {
            "id": gold.id,
            "parse_error": jr.parse_error,
        }
        if not jr.parse_error:
            item_d["judge_scores"] = {
                dim: jr.scores[dim].score for dim in DIMENSIONS if dim in jr.scores
            }
            item_d["human_labels"] = gold.human_labels
            item_d["agreements"] = {
                dim: (
                    jr.scores[dim].score == gold.human_labels.get(dim)
                    if gold.human_labels.get(dim) in VALID_SCORES
                    else None
                )
                for dim in DIMENSIONS
                if dim in jr.scores
            }
        items_detail.append(item_d)

    return {
        "judge_model": judge_model,
        "temperature": temperature,
        "n_gold_items": len(gold_items),
        "parse_failures": parse_failures,
        "agreement_threshold": threshold,
        "per_dimension_agreement": {k: round(v, 4) for k, v in agreement.items()},
        "below_threshold_dimensions": below_threshold,
        "self_consistency": {k: round(v, 4) for k, v in consistency.items()},
        "items": items_detail,
    }
