from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.core.schema_validate`.
"""

from .core.schema_validate import validate_json_schema

__all__ = ["validate_json_schema"]

