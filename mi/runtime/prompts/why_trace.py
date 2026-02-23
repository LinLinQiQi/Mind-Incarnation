from __future__ import annotations

from typing import Any

from ._util import _to_json


def why_trace_prompt(
    *,
    target: dict[str, Any],
    as_of_ts: str,
    candidate_claims: list[dict[str, Any]],
    notes: str,
) -> str:
    ids = [
        str(c.get("claim_id") or "").strip()
        for c in (candidate_claims or [])
        if isinstance(c, dict) and str(c.get("claim_id") or "").strip()
    ]
    return "\n".join(
        [
            "You are MI (Mind Incarnation).",
            "Perform root-cause tracing: select a minimal support set of atomic Claims that best explain a target decision/action/event.",
            "",
            "Rules:",
            "- Output MUST be a single JSON object matching the provided JSON Schema.",
            "- No markdown, no extra keys, no extra commentary.",
            "- You MUST select claim ids ONLY from the provided candidate_claim_ids list.",
            "- Choose the MINIMAL set of claims that explains the target (usually 1-5).",
            "- Consider temporal validity: as_of_ts must fall within valid_from/valid_to when present; avoid out-of-window claims.",
            "- If insufficient evidence, set status=insufficient, chosen_claim_ids=[], and explain what's missing.",
            "",
            "as_of_ts:",
            (as_of_ts or "").strip(),
            "",
            "Target (verbatim JSON):",
            _to_json(target if isinstance(target, dict) else {}),
            "",
            "candidate_claim_ids (MUST choose only from here):",
            _to_json(ids),
            "",
            "Candidate claims (compact JSON):",
            _to_json(candidate_claims),
            "",
            "Run notes:",
            (notes or "").strip(),
            "",
            "Now output the WhyTrace JSON.",
        ]
    ).strip() + "\n"

