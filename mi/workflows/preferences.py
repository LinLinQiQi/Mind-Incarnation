from __future__ import annotations

import hashlib
from typing import Any

from ..core.paths import ProjectPaths
from ..core.storage import read_json_best_effort, write_json_atomic


PREFERENCE_CANDIDATES_VERSION = "v1"


def _normalize_pref_text(text: str) -> str:
    # Keep this stable and simple: whitespace-collapsed, lowercased.
    return " ".join((text or "").strip().split()).lower()


def preference_signature(*, scope: str, text: str) -> str:
    data = f"{(scope or '').strip().lower()}\n{_normalize_pref_text(text)}"
    digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
    return digest[:16]


def load_preference_candidates(project_paths: ProjectPaths, *, warnings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    obj = read_json_best_effort(project_paths.preference_candidates_path, default=None, label="preference_candidates", warnings=warnings)
    if not isinstance(obj, dict):
        return {"version": PREFERENCE_CANDIDATES_VERSION, "by_signature": {}}
    if "by_signature" not in obj or not isinstance(obj.get("by_signature"), dict):
        obj["by_signature"] = {}
    if "version" not in obj:
        obj["version"] = PREFERENCE_CANDIDATES_VERSION
    return obj


def write_preference_candidates(project_paths: ProjectPaths, obj: dict[str, Any]) -> None:
    if not isinstance(obj, dict):
        raise TypeError("preference candidates must be a dict")
    if "version" not in obj:
        obj["version"] = PREFERENCE_CANDIDATES_VERSION
    if "by_signature" not in obj or not isinstance(obj.get("by_signature"), dict):
        obj["by_signature"] = {}
    write_json_atomic(project_paths.preference_candidates_path, obj)
