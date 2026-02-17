from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.thoughtdb.global_ledger`.
"""

from .thoughtdb.global_ledger import append_global_event, iter_global_events

__all__ = ["append_global_event", "iter_global_events"]

