from __future__ import annotations


# Tags for "pinned" preference/goal claims that should be included in compact Thought DB contexts
# even if they are not among the most recent preferences.

TESTLESS_STRATEGY_TAG = "mi:testless_verification_strategy"

# Preference/goal claim tags that are important for MI's operational decisions.
PINNED_PREF_GOAL_TAGS: set[str] = {
    TESTLESS_STRATEGY_TAG,
}

