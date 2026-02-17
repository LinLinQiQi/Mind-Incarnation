from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.thoughtdb.why`.
"""

from .thoughtdb.why import (
    WhyTraceOutcome,
    collect_candidate_claims,
    default_as_of_ts,
    find_evidence_event,
    query_from_evidence_event,
    run_why_trace,
)

__all__ = [
    "WhyTraceOutcome",
    "collect_candidate_claims",
    "default_as_of_ts",
    "find_evidence_event",
    "query_from_evidence_event",
    "run_why_trace",
]

