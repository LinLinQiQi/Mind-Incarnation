from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.core.config`.
"""

from .core.config import (
    apply_config_template,
    config_for_display,
    config_path,
    default_config,
    get_config_template,
    init_config,
    list_config_templates,
    load_config,
    load_config_raw,
    resolve_api_key,
    rollback_config,
    validate_config,
)

__all__ = [
    "apply_config_template",
    "config_for_display",
    "config_path",
    "default_config",
    "get_config_template",
    "init_config",
    "list_config_templates",
    "load_config",
    "load_config_raw",
    "resolve_api_key",
    "rollback_config",
    "validate_config",
]

