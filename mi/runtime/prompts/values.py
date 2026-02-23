from __future__ import annotations

from typing import Any

from ._util import _to_json


def compile_values_prompt(*, values_text: str) -> str:
    return "\n".join(
        [
            "You are MI (Mind Incarnation).",
            "Compile user values/preferences into a compact structured summary (V1).",
            "",
            "Hard constraints (must respect in your decision procedure):",
            "- MI sits above Hands (execution agent; V1 default: Codex CLI) and only controls input + reads output.",
            "- MI does NOT intercept or gate Hands tool execution.",
            "- MI does NOT force Hands into step-by-step protocols.",
            "- Refactor intent is behavior-preserving by default unless explicitly requested otherwise.",
            "- If a project has no tests, MI asks once per project for a testless verification strategy, then remembers it.",
            "- MI does not ask for 'completion confirmation' by default; MI self-evaluates done/blocked.",
            "",
            "Output rules:",
            "- Output MUST be a single JSON object matching the provided JSON Schema.",
            "- No markdown, no code fences, no extra keys, no extra commentary.",
            "- Keep values_summary short (5-12 bullets).",
            "- decision_procedure.mermaid MUST be a valid Mermaid flowchart (flowchart TD ...).",
            "",
            "User values/preferences text (verbatim):",
            values_text.strip(),
            "",
            "Now output the compiled values JSON.",
        ]
    ).strip() + "\n"


def values_claim_patch_prompt(
    *,
    values_text: str,
    compiled_values: dict[str, Any],
    existing_values_claims: list[dict[str, Any]],
    allowed_event_ids: list[str],
    allowed_retract_claim_ids: list[str],
    notes: str,
) -> str:
    """Build a prompt that migrates/updates user values into Thought DB preference/goal claims.

    Output must follow `schemas/values_claim_patch.json`.
    """

    allowed = [str(x) for x in (allowed_event_ids or []) if str(x).strip()][:40]
    retractable = [str(x) for x in (allowed_retract_claim_ids or []) if str(x).strip()][:400]
    return "\n".join(
        [
            "You are MI (Mind Incarnation).",
            "Update the user's global values/preferences as reusable atomic Claims in the Thought DB.",
            "",
            "Goal:",
            "- Produce a SMALL patch that evolves existing value claims into the new set implied by values_text.",
            "- Use supersedes/same_as edges to preserve history and avoid broken references.",
            "",
            "Hard constraints:",
            "- Only use the provided values_text and existing_values_claims as input (do not invent).",
            "- Every NEW claim MUST cite 1-5 EvidenceLog event_id(s) from the allowed list.",
            "- Every edge MUST cite 1-5 EvidenceLog event_id(s) from the allowed list.",
            "- Prefer claim_type=preference or goal for values. Avoid fact/assumption unless truly needed.",
            "- New value claims MUST be scope=global and visibility=global.",
            "- Include the stable tag 'values:base' on every NEW value claim.",
            "- Also include a tag 'values_set:<event_id>' for traceability.",
            "- Keep the patch minimal: only create new claims when needed; reuse existing claims via same_as when equivalent.",
            "- To update a claim's meaning/text, create a NEW claim and add supersedes(old_claim_id -> new_local_id).",
            "",
            "Retractions:",
            "- If an existing value claim is clearly removed or invalidated by the new values_text, add its claim_id to retract_claim_ids.",
            "- You may ONLY retract claim_ids from the provided allowed_retract_claim_ids list.",
            "- Be conservative: prefer supersedes over retract when it is a refinement rather than a full removal.",
            "",
            "Output rules:",
            "- Output MUST be a single JSON object matching the provided JSON Schema.",
            "- No markdown, no extra keys, no extra commentary.",
            "- local_id values MUST be unique (e.g., c1, c2). Edges may reference existing claim_id or local_id.",
            "",
            "values_text (verbatim):",
            (values_text or "").strip(),
            "",
            "compiled_values (structured, best-effort; may be empty):",
            _to_json(compiled_values if isinstance(compiled_values, dict) else {}),
            "",
            "existing_values_claims (active canonical, compact):",
            _to_json(existing_values_claims if isinstance(existing_values_claims, list) else []),
            "",
            "Allowed EvidenceLog event_id list (MUST cite only from here):",
            _to_json(allowed),
            "",
            "allowed_retract_claim_ids:",
            _to_json(retractable),
            "",
            "Run notes:",
            (notes or "").strip(),
            "",
            "Now output the values claim patch JSON.",
        ]
    ).strip() + "\n"

