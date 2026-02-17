from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.runtime.runner`.
"""

from .runtime.runner import AutopilotResult, run_autopilot

__all__ = ["AutopilotResult", "run_autopilot"]

