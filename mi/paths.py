from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.core.paths`.
"""

from .core.paths import (
    GlobalPaths,
    ProjectPaths,
    default_home_dir,
    project_id_for_root,
    project_identity,
    project_index_path,
    resolve_cli_project_root,
)

__all__ = [
    "GlobalPaths",
    "ProjectPaths",
    "default_home_dir",
    "project_id_for_root",
    "project_identity",
    "project_index_path",
    "resolve_cli_project_root",
]

