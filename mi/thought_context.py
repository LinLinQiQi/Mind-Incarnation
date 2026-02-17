from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.thoughtdb.context`.
"""

from .thoughtdb.context import ThoughtDbContext, build_decide_next_thoughtdb_context

__all__ = ["ThoughtDbContext", "build_decide_next_thoughtdb_context"]

