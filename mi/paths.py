from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path


def default_home_dir() -> Path:
    return Path(os.environ.get("MI_HOME") or Path.home() / ".mind-incarnation")


def project_id_for_root(root: Path) -> str:
    root_str = str(root.resolve())
    digest = hashlib.sha256(root_str.encode("utf-8")).hexdigest()
    return digest[:12]


@dataclass(frozen=True)
class ProjectPaths:
    home_dir: Path
    project_root: Path

    @property
    def project_id(self) -> str:
        return project_id_for_root(self.project_root)

    @property
    def project_dir(self) -> Path:
        return self.home_dir / "projects" / self.project_id

    @property
    def overlay_path(self) -> Path:
        return self.project_dir / "overlay.json"

    @property
    def evidence_log_path(self) -> Path:
        return self.project_dir / "evidence.jsonl"

    @property
    def learned_path(self) -> Path:
        return self.project_dir / "learned.jsonl"

    @property
    def transcripts_dir(self) -> Path:
        return self.project_dir / "transcripts"


@dataclass(frozen=True)
class GlobalPaths:
    home_dir: Path

    @property
    def minds_dir(self) -> Path:
        return self.home_dir / "mindspec"

    @property
    def base_path(self) -> Path:
        return self.minds_dir / "base.json"

    @property
    def learned_path(self) -> Path:
        return self.minds_dir / "learned.jsonl"
