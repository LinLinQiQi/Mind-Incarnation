from __future__ import annotations

from typing import Any

from ._util import _to_json


def extract_evidence_prompt(
    *,
    task: str,
    hands_provider: str,
    light_injection: str,
    batch_input: str,
    hands_batch_summary: dict[str, Any],
    repo_observation: dict[str, Any],
) -> str:
    return "\n".join(
        [
            "You are MI (Mind Incarnation).",
            "Extract durable evidence from a Hands batch run.",
            "",
            "Rules:",
            "- Output MUST be a single JSON object matching the provided JSON Schema.",
            "- No markdown, no extra keys, no extra commentary.",
            "- Be concise: keep strings short and factual.",
            "",
            f"Hands provider: {hands_provider.strip() or '(unknown)'}",
            "",
            "MI light injection (what Hands was told):",
            light_injection.strip(),
            "",
            "Batch input sent to Hands (verbatim):",
            (batch_input or "").strip(),
            "",
            "User task:",
            task.strip(),
            "",
            "Hands batch summary (machine extracted):",
            _to_json(hands_batch_summary),
            "",
            "Repo observation (read-only heuristic):",
            _to_json(repo_observation),
            "",
        ]
    ).strip() + "\n"

