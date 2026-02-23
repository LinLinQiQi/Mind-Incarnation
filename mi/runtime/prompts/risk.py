from __future__ import annotations

from typing import Any

from ._util import _to_json


def risk_judge_prompt(
    *,
    task: str,
    hands_provider: str,
    runtime_cfg: dict[str, Any],
    project_overlay: dict[str, Any],
    thought_db_context: dict[str, Any] | None,
    risk_signals: list[str],
    hands_last_message: str,
) -> str:
    return "\n".join(
        [
            "You are MI (Mind Incarnation).",
            "Assess risk for a Hands batch using user values/preferences and evidence.",
            "",
            "Rules:",
            "- Output MUST be a single JSON object matching the provided JSON Schema.",
            "- No markdown, no extra keys, no extra commentary.",
            "- If information is insufficient, set severity to 'medium' and should_ask_user=true.",
            "",
            "User task:",
            task.strip(),
            "",
            f"Hands provider: {hands_provider.strip() or '(unknown)'}",
            "",
            "Runtime config (structured):",
            _to_json(runtime_cfg),
            "",
            "ProjectOverlay:",
            _to_json(project_overlay),
            "",
            "Thought DB context (canonical values/preferences; may be empty):",
            _to_json(thought_db_context if isinstance(thought_db_context, dict) else {}),
            "",
            "Detected risk signals (heuristic, may be empty):",
            _to_json(risk_signals),
            "",
            "Hands last message (raw):",
            hands_last_message.strip(),
            "",
            "Now output the risk judgement.",
        ]
    ).strip() + "\n"

