from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.providers.llm`.
"""

from .providers.llm import MiLlm, MiPromptResult, _extract_json

__all__ = ["MiLlm", "MiPromptResult", "_extract_json"]

