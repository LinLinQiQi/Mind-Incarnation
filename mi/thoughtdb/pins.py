from __future__ import annotations


# Tags for "pinned" preference/goal claims that should be included in compact Thought DB contexts
# even if they are not among the most recent preferences.

TESTLESS_STRATEGY_TAG = "mi:testless_verification_strategy"

# Canonical operational defaults (stored as preference claims; project may override global).
ASK_WHEN_UNCERTAIN_TAG = "mi:setting:ask_when_uncertain"
REFACTOR_INTENT_TAG = "mi:setting:refactor_intent"

# Preference/goal claim tags that are important for MI's operational decisions.
PINNED_PREF_GOAL_TAGS: set[str] = {
    TESTLESS_STRATEGY_TAG,
    ASK_WHEN_UNCERTAIN_TAG,
    REFACTOR_INTENT_TAG,
}
