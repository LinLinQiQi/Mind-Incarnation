from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.providers.mind_errors`.
"""

from .providers.mind_errors import MindCallError

__all__ = ["MindCallError"]

