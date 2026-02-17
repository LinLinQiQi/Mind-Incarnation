from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.thoughtdb.pins`.
"""

from .thoughtdb.pins import (
    ASK_WHEN_UNCERTAIN_TAG,
    PINNED_PREF_GOAL_TAGS,
    REFACTOR_INTENT_TAG,
    TESTLESS_STRATEGY_TAG,
)

__all__ = [
    "ASK_WHEN_UNCERTAIN_TAG",
    "PINNED_PREF_GOAL_TAGS",
    "REFACTOR_INTENT_TAG",
    "TESTLESS_STRATEGY_TAG",
]

