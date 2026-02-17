from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.runtime.inspect`.
"""

from .runtime.inspect import (
    classify_evidence_record,
    load_last_batch_bundle,
    summarize_evidence_record,
    tail_json_objects,
    tail_raw_lines,
)

__all__ = [
    "classify_evidence_record",
    "load_last_batch_bundle",
    "summarize_evidence_record",
    "tail_json_objects",
    "tail_raw_lines",
]

