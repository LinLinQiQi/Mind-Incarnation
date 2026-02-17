from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.providers.mind_anthropic`.
"""

from .providers.mind_anthropic import AnthropicMindProvider

__all__ = ["AnthropicMindProvider"]

