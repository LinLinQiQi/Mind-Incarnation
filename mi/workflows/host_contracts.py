from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class HostBinding:
    host: str
    workspace_root: Path
    enabled: bool
    generated_rel_dir: str
    register_symlink_dirs: list[dict[str, str]]  # [{"src": "...", "dst": "..."}]

    @property
    def generated_root(self) -> Path:
        return self.workspace_root / self.generated_rel_dir


class HostAdapter:
    """Host adapter interface (derived artifacts + best-effort registration).

    MI's source of truth is stored under MI home. Anything written into a host
    workspace is a derived, regeneratable artifact.
    """

    host: str

    def __init__(self, host: str):
        self.host = str(host or "").strip().lower()

    def generate(self, *, binding: HostBinding, project_id: str, workflows: list[dict[str, Any]]) -> tuple[list[str], dict[str, Any]]:
        """Generate host-specific derived artifacts under binding.generated_root.

        Returns (new_files_rel, ctx) where new_files_rel are paths relative to generated_root.
        """

        return [], {}

    def register(
        self,
        *,
        binding: HostBinding,
        prev_manifest: dict[str, Any],
        gen_root: Path,
        ctx: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Perform host-specific registration (e.g., symlinks) and return (details, manifest_state)."""

        return {"ok": True}, {}

