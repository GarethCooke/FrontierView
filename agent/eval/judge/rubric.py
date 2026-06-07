"""
Layer 3 rubric: anchored discrete scoring dimensions for reasoning quality.

Each dimension is scored independently as fail / partial / pass (or na when the
dimension does not apply to the trace).  The judge must cite a short verbatim
trace span to justify every score.
"""
from __future__ import annotations

DIMENSIONS = [
    "assumption_handling",
    "explanation_fidelity",
    "out_of_tool_handling",
    "confidence_calibration",
    "domain_correctness",
]

RUBRIC: dict[str, dict] = {
    "assumption_handling": {
        "description": (
            "On an underspecified question, did the agent state its assumption or ask "
            "for clarification, rather than silently filling in missing parameters? "
            "If the question is fully specified, default to pass."
        ),
        "anchors": {
            "pass": (
                "Agent explicitly labels its choice as an assumption "
                "('I'll assume a TWAP schedule since none was specified'), "
                "or asks the user to clarify before proceeding."
            ),
            "partial": (
                "Agent makes a reasonable, defensible default choice but does not label "
                "it as an assumption. The choice is recoverable from context but not flagged."
            ),
            "fail": (
                "Agent silently fills in an unspecified parameter with no acknowledgement; "
                "or makes a clearly wrong default (e.g. assumes a 6.5-hour full-day horizon "
                "for a question that says 'quickly')."
            ),
        },
        "exemplars": [
            {
                "trace_span": (
                    "Question: 'What's the impact of buying 100k AAPL?' "
                    "Answer: 'I'll assume a TWAP schedule over 2 hours since neither the "
                    "schedule type nor the horizon was specified...'"
                ),
                "score": "pass",
                "reason": (
                    "Names both missing parameters and labels the choice as an assumption."
                ),
            },
            {
                "trace_span": (
                    "Question: 'What's the impact of buying 100k AAPL?' "
                    "[tool_call cost_and_variance(schedule='twap', horizon_hours=2.0)] "
                    "Answer: 'The execution cost is 8.3 bps.'"
                ),
                "score": "fail",
                "reason": (
                    "Called cost_and_variance with TWAP/2h without acknowledging "
                    "those parameters were not specified."
                ),
            },
        ],
    },

    "explanation_fidelity": {
        "description": (
            "Does the prose correctly characterise what the tools returned? "
            "Catches misattribution (e.g. calling a temporary-impact term permanent), "
            "over/under-claiming, and narrative that contradicts the numbers. "
            "Layer 2 checks that numbers exist; this dimension checks the story about them."
        ),
        "anchors": {
            "pass": (
                "All prose claims are consistent with the tool result summaries. "
                "Temporary, permanent, and spread costs are labelled correctly. "
                "The direction of effects (higher eta → higher cost) is stated correctly."
            ),
            "partial": (
                "One minor imprecision or omission that does not fundamentally mislead — "
                "e.g. rounding a number slightly differently in prose, or omitting a "
                "secondary cost component while correctly reporting the total."
            ),
            "fail": (
                "Prose contradicts or misattributes tool results. Examples: calling "
                "temporary impact 'permanent impact'; reporting a cost materially "
                "different from the tool result; claiming front-loaded is cheaper "
                "when the tool shows back-loaded is cheaper."
            ),
        },
        "exemplars": [
            {
                "trace_span": (
                    "[tool_result cost_and_variance summary: "
                    "{temporary_bps: 6.2, permanent_bps: 1.4, spread_bps: 0.8, total_bps: 8.4}] "
                    "Answer: 'Cost breaks down as 6.2 bps temporary impact, "
                    "1.4 bps permanent impact, and 0.8 bps half-spread.'"
                ),
                "score": "pass",
                "reason": "Prose matches tool output exactly with correct component labels.",
            },
            {
                "trace_span": (
                    "[tool_result cost_and_variance summary: "
                    "{temporary_bps: 6.2, permanent_bps: 1.4}] "
                    "Answer: 'The permanent impact is 6.2 bps and the temporary impact is 1.4 bps.'"
                ),
                "score": "fail",
                "reason": "Swapped temporary and permanent impact labels.",
            },
        ],
    },

    "out_of_tool_handling": {
        "description": (
            "For requests the toolset cannot satisfy, did the agent decline or caveat "
            "substantively and explain why, rather than fabricate or reflexively refuse? "
            "If the question is fully in-tool scope, default to pass."
        ),
        "anchors": {
            "pass": (
                "Agent declines and names what is out of scope and why "
                "('FrontierView models execution cost but does not predict prices — "
                "that requires a separate alpha model'). "
                "Optionally redirects to related in-scope tools."
            ),
            "partial": (
                "Agent declines but with a generic refusal that does not explain the "
                "scope limitation. The user learns the agent won't help but not why."
            ),
            "fail": (
                "Agent fabricates an answer to a request the tools cannot satisfy — "
                "invents calibrated parameters, predicts price, or generates an "
                "autonomous trading algorithm without caveating that no such tool exists."
            ),
        },
        "exemplars": [
            {
                "trace_span": (
                    "Question: 'Calibrate the model to my fills.' "
                    "Answer: 'FrontierView does not expose a calibration tool — η and γ "
                    "are stored reference values, not derived from live fills. To use your "
                    "data you would run the recovery routine separately and update the "
                    "stored parameters.'"
                ),
                "score": "pass",
                "reason": (
                    "Declines with an accurate explanation of the scope limitation "
                    "and points to an alternative path."
                ),
            },
            {
                "trace_span": (
                    "Question: 'Calibrate the model to my fills.' "
                    "Answer: 'I\\'m sorry, I can\\'t help with that request.'"
                ),
                "score": "partial",
                "reason": "Declines but gives no explanation of why or what is in scope.",
            },
            {
                "trace_span": (
                    "Question: 'Calibrate the model to my fills.' "
                    "[no tool_call] "
                    "Answer: 'Based on your fills I estimate η = 0.12 and γ = 0.03.'"
                ),
                "score": "fail",
                "reason": "Fabricates calibrated parameters with no tool backing.",
            },
        ],
    },

    "confidence_calibration": {
        "description": (
            "Does the agent hedge appropriately? Appropriate means hedging on genuinely "
            "uncertain outputs (synthetic results, large-order warnings, structural-parameter "
            "sweeps) without hedging unnecessarily on precise model-computed quantities."
        ),
        "anchors": {
            "pass": (
                "Agent hedges precisely where uncertainty is warranted — synthetic caveat, "
                ">ADV reliability warning, sensitivity caveat on exponent changes — and "
                "states model-computed figures with appropriate confidence."
            ),
            "partial": (
                "Slight miscalibration that does not fundamentally mislead: hedges a "
                "synthetic result but not as prominently as warranted, or omits one "
                "minor caveat while correctly reporting the main numbers."
            ),
            "fail": (
                "Overconfident: states a synthetic or uncertain result as a definitive "
                "market fact. Or over-hedged: qualifies a precise model output so heavily "
                "that the answer is effectively obscured."
            ),
        },
        "exemplars": [
            {
                "trace_span": (
                    "[tool_result summary: {warning: 'order_size exceeds ADV; "
                    "impact estimates unreliable'}] "
                    "Answer: 'Note: this order (~1× ADV) is outside the model\\'s reliable "
                    "range — treat these estimates as indicative only.'"
                ),
                "score": "pass",
                "reason": "Accurately propagates the model's own reliability warning.",
            },
            {
                "trace_span": (
                    "[tool_result summary: {synthetic: true, eta_recovered: 0.14}] "
                    "Answer: 'The market-calibrated η is 0.14.'"
                ),
                "score": "fail",
                "reason": (
                    "States a synthetic recovery result as 'market-calibrated' "
                    "with no uncertainty caveat."
                ),
            },
        ],
    },

    "domain_correctness": {
        "description": (
            "Checked ONLY against the four anchored domain exemplars below. "
            "Do not apply independent quant finance knowledge — score only against "
            "the supplied anchors."
        ),
        "anchors": {
            "pass": (
                "All four domain anchors satisfied: correct citation (Almgren et al. 2005), "
                "permanent impact treated as linear (β=1), no claim that the AC closed-form "
                "schedule is optimal under the 0.6 power-law, and synthetic recovery framed "
                "as from planted parameters (not market-calibrated)."
            ),
            "partial": (
                "One minor anchor slip that does not affect the core quantitative reasoning — "
                "e.g. drops the year from the citation but makes no false mechanics claim."
            ),
            "fail": (
                "One or more material anchor violations: wrong citation year, claims permanent "
                "impact is nonlinear, asserts AC schedule is optimal under the power-law "
                "functional form, or frames synthetic recovery as market data."
            ),
        },
        "domain_anchors": [
            {
                "id": "citation",
                "rule": "Cite 'Almgren et al. (2005)', not 'Almgren-Chriss 2001'.",
                "pass_example": "'...based on Almgren et al. (2005)...'",
                "fail_example": "'...the Almgren-Chriss (2001) framework...'",
            },
            {
                "id": "permanent_linear",
                "rule": (
                    "Permanent impact is linear (β=1) per the 2005 no-arbitrage constraint. "
                    "Flag if the agent implies permanent impact is nonlinear or power-law."
                ),
                "pass_example": "'Permanent impact is linear in trade size per the no-arbitrage constraint.'",
                "fail_example": "'Permanent impact follows a power-law with exponent 0.6...'",
            },
            {
                "id": "ac_optimality",
                "rule": (
                    "Do NOT claim the AC closed-form schedule is optimal under the 0.6 "
                    "power-law temporary-impact functional form. The AC schedule is derived "
                    "under linear temporary impact; under the power-law it sits above the "
                    "true efficient frontier."
                ),
                "pass_example": (
                    "'The AC-optimal schedule minimises cost-variance under linear temporary "
                    "impact; under the 0.6 power-law exponent it is an approximation.'"
                ),
                "fail_example": (
                    "'The Almgren-Chriss optimal schedule is globally optimal under "
                    "the power-law impact model.'"
                ),
            },
            {
                "id": "synthetic_recovery",
                "rule": (
                    "Synthetic recovery produces estimates from planted parameters, NOT from "
                    "market data. Frame as 'recovered from synthetic trades' or 'estimated "
                    "from planted parameters', never as 'market-calibrated'."
                ),
                "pass_example": "'η recovered from synthetic trades generated with planted parameters.'",
                "fail_example": "'Based on market calibration, η = 0.14.'",
            },
        ],
        "exemplars": [
            {
                "trace_span": (
                    "Answer: 'FrontierView implements Almgren et al. (2005) with linear "
                    "permanent impact (β=1 per the no-arbitrage constraint).'"
                ),
                "score": "pass",
                "reason": "Correct citation and correct permanent-impact linearity assertion.",
            },
            {
                "trace_span": (
                    "Answer: 'The Almgren-Chriss (2001) optimal schedule minimises "
                    "the cost-variance trade-off.'"
                ),
                "score": "fail",
                "reason": "Wrong citation year.",
            },
        ],
    },
}
