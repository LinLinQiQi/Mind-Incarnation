from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.providers.hands_cli`.
"""

from .providers.hands_cli import CliHandsAdapter, CliRunResult

__all__ = ["CliHandsAdapter", "CliRunResult"]

