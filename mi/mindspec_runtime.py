from __future__ import annotations

from typing import Any


def sanitize_mindspec_base_for_runtime(base: dict[str, Any] | None) -> dict[str, Any]:
    """Return a sanitized MindSpec base for runtime Mind prompts.

    Goal: prevent value text duplication/contradiction.

    - Canonical values/preferences live in Thought DB preference/goal Claims.
    - `values_text` / `values_summary` remain useful for humans and `mi init`, but
      should not steer runtime decisions once claims exist.
    """

    if not isinstance(base, dict):
        return {}

    out: dict[str, Any] = dict(base)

    # Remove value text from runtime prompts so the model relies on Thought DB context.
    out["values_text"] = ""
    out["values_summary"] = []

    return out

