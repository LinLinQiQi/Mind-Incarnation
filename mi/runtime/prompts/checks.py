from __future__ import annotations

from typing import Any

from ._util import _to_json


def plan_min_checks_prompt(
    *,
    task: str,
    hands_provider: str,
    runtime_cfg: dict[str, Any],
    project_overlay: dict[str, Any],
    thought_db_context: dict[str, Any] | None,
    recent_evidence: list[dict[str, Any]],
    repo_observation: dict[str, Any],
) -> str:
    return "\n".join(
        [
            "You are MI (Mind Incarnation).",
            "Plan minimal, high-information verification checks to reduce uncertainty.",
            "",
            "Constraints:",
            "- MI does NOT run checks directly; Hands should execute checks when instructed.",
            "- Prefer existing project checks (tests/build/lint/typecheck) over introducing new tooling.",
            "- You may suggest generating a minimal smoke test only when it is low-cost and aligned with values.",
            "- If the project has no tests and verification is needed: first look for a canonical preference Claim in Thought DB context tagged 'mi:testless_verification_strategy'. If present, use it and set needs_testless_strategy=false. Otherwise, set needs_testless_strategy=true and ask the user ONCE per project for a testless verification strategy.",
            "- Do NOT ask the user to confirm completion; only ask when blocked by missing info.",
            "",
            "Output rules:",
            "- Output MUST be a single JSON object matching the provided JSON Schema.",
            "- No markdown, no extra keys, no extra commentary.",
            "- If no checks are needed, set should_run_checks=false and hands_check_input=\"\".",
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
            "Repo observation (read-only heuristic):",
            _to_json(repo_observation),
            "",
            "Recent evidence (most recent last):",
            _to_json(recent_evidence),
            "",
            "Now plan the minimal checks and produce a Hands instruction if needed.",
        ]
    ).strip() + "\n"

