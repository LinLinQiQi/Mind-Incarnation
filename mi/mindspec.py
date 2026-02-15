from __future__ import annotations

from dataclasses import dataclass
import secrets
import time
from pathlib import Path
from typing import Any

from .paths import GlobalPaths, ProjectPaths, default_home_dir, project_identity
from .storage import append_jsonl, now_rfc3339, read_json, write_json, iter_jsonl


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def _default_base(values_text: str) -> dict[str, Any]:
    # Keep this minimal and user-editable; most decisions are still made by MI prompts at runtime.
    return {
        "version": "v1",
        "values_text": values_text,
        "values_summary": [],
        "decision_procedure": {
            "summary": (
                "MI controls only input/output around Hands (no tool interception, no forced step slicing). "
                "MI runs in batches, logs evidence, suggests minimal checks, and asks the user only when needed."
            ),
            "mermaid": (
                "flowchart TD\n"
                "  U[User task + values] --> S[Load MindSpec/Overlay/Learned]\n"
                "  S --> I[Build light injection + task input]\n"
                "  I --> C[Run Hands (free execution)]\n"
                "  C --> T[Capture raw transcript]\n"
                "  T --> E[Extract evidence]\n"
                "  E --> D[Decide next / risk]\n"
                "  D -->|need missing info| Q[Ask user (minimal)]\n"
                "  D -->|continue| I\n"
                "  D -->|done/blocked| END[Stop]\n"
            ),
        },
        "defaults": {
            "refactor_intent": "behavior_preserving",
            "ask_when_uncertain": True,
        },
        "verification": {
            "no_tests_policy": "ask_once_per_project_then_remember",
        },
        "external_actions": {
            "network_policy": "values_judged",
            "install_policy": "values_judged",
        },
        "interrupt": {
            "mode": "off",
            "signal_sequence": ["SIGINT", "SIGTERM", "SIGKILL"],
            "escalation_ms": [2000, 5000],
        },
        "transparency": {
            "store_raw_transcript": True,
            "store_evidence_log": True,
            "ui_expandable_transcript": True,
        },
        "workflows": {
            # Workflow solidification is project-scoped in V1 by default, but workflows may also be global.
            # Host workspaces get only derived artifacts via adapters.
            "auto_mine": True,
            "auto_enable": True,
            # Usually require >=2 occurrences before solidifying; allow 1 when benefit is high.
            "min_occurrences": 2,
            "allow_single_if_high_benefit": True,
            # When a workflow change is detected during `mi run`, sync derived artifacts to host workspaces.
            "auto_sync_on_change": True,
        },
        "cross_project_recall": {
            # Default: enabled but very conservative. Uses snapshot/learned/workflow + text search (no embeddings required).
            "enabled": True,
            "top_k": 3,
            "max_chars": 1800,
            "include_kinds": ["snapshot", "learned", "workflow"],
            # Prefer recalling within the current project first (then global, then other projects).
            # Set to true if you want "pure" cross-project-only recall.
            "exclude_current_project": False,
            "prefer_current_project": True,
            "triggers": {
                "run_start": True,
                "before_ask_user": True,
                "risk_signal": True,
            },
        },
        "preference_mining": {
            # Preference prediction: mine possible learned rules from MI-captured transcript/evidence.
            "auto_mine": True,
            # Usually require >=2 occurrences before suggesting/applying; allow 1 when benefit is high.
            "min_occurrences": 2,
            "allow_single_if_high_benefit": True,
            # Skip low-confidence suggestions to reduce noisy learning.
            "min_confidence": 0.75,
            "max_suggestions": 3,
        },
        "violation_response": {
            "auto_learn": True,
            "prompt_user_on_high_risk": True,
            # If set, MI will prompt the user only for matching severities/categories.
            # Empty categories means "any category".
            "prompt_user_risk_severities": ["high", "critical"],
            "prompt_user_risk_categories": [],
            # When true, MI prompts only when risk_judge.should_ask_user=true (recommended).
            "prompt_user_respect_should_ask_user": True,
        },
    }


@dataclass(frozen=True)
class LoadedMindSpec:
    base: dict[str, Any]
    learned_text: str
    project_overlay: dict[str, Any]

    def light_injection(self) -> str:
        values_text = (self.base.get("values_text") or "").strip()
        values_summary = self.base.get("values_summary") or []
        if isinstance(values_summary, list) and any(str(x).strip() for x in values_summary):
            values = "User values/preferences (summary):\n" + "\n".join([f"- {str(x).strip()}" for x in values_summary if str(x).strip()])
        else:
            values = values_text
        learned = (self.learned_text or "").strip()

        # "Light injection": enough to steer behavior and reduce unnecessary questions,
        # but does not impose a step protocol.
        parts: list[str] = []
        parts.append("[MI Light Injection]")
        if values:
            parts.append("User values/preferences (high-level):")
            parts.append(values)
        dp = self.base.get("decision_procedure") or {}
        if isinstance(dp, dict):
            dp_summary = str(dp.get("summary") or "").strip()
            if dp_summary:
                parts.append("Decision procedure (summary):")
                parts.append(dp_summary)
        if learned:
            parts.append("Learned preferences (reversible):")
            parts.append(learned)

        defaults = self.base.get("defaults", {})
        refactor_intent = defaults.get("refactor_intent", "behavior_preserving")
        ask_when_uncertain = defaults.get("ask_when_uncertain", True)

        parts.append("Defaults:")
        parts.append(f"- Refactor intent: {refactor_intent} (unless explicitly requested otherwise)")
        parts.append(f"- When uncertain: {'ask' if ask_when_uncertain else 'proceed'}")
        parts.append("- If a potentially external action (network/install/push/publish) is NOT clearly covered, pause and ask.")

        return "\n".join(parts).strip() + "\n"


class MindSpecStore:
    def __init__(self, home_dir: str | None):
        home = Path(home_dir) if home_dir else default_home_dir()
        self._paths = GlobalPaths(home_dir=home)

    @property
    def home_dir(self) -> Path:
        return self._paths.home_dir

    @property
    def base_path(self) -> Path:
        return self._paths.base_path

    @property
    def learned_path(self) -> Path:
        return self._paths.learned_path

    def write_base_values(self, values_text: str) -> None:
        write_json(self.base_path, _default_base(values_text=values_text))

    def write_base(self, base_obj: dict[str, Any]) -> None:
        values_text = str(base_obj.get("values_text") or "") if isinstance(base_obj, dict) else ""
        defaults = _default_base(values_text=values_text)
        merged = _deep_merge(defaults, base_obj if isinstance(base_obj, dict) else {})
        write_json(self.base_path, merged)

    def load_base(self) -> dict[str, Any]:
        raw = read_json(self.base_path, default=None)
        if not isinstance(raw, dict):
            return _default_base(values_text="")
        values_text = str(raw.get("values_text") or "")
        defaults = _default_base(values_text=values_text)
        return _deep_merge(defaults, raw)

    def append_learned(self, *, project_root: Path, scope: str, text: str, rationale: str) -> str:
        entry_id = f"lc_{time.time_ns()}_{secrets.token_hex(4)}"
        if scope == "project":
            target = ProjectPaths(home_dir=self._paths.home_dir, project_root=project_root).learned_path
        else:
            target = self.learned_path
        append_jsonl(
            target,
            {
                "id": entry_id,
                "ts": now_rfc3339(),
                "scope": scope,
                "enabled": True,
                "text": text,
                "rationale": rationale,
            },
        )
        return entry_id

    def load_learned_text(self, project_root: Path) -> str:
        project_learned_path = ProjectPaths(home_dir=self._paths.home_dir, project_root=project_root).learned_path
        sources = (self.learned_path, project_learned_path)

        # Pass 1: gather disables so later project-scoped rollbacks can mask earlier global entries.
        disabled: set[str] = set()
        for source in sources:
            for entry in iter_jsonl(source):
                if entry.get("action") == "disable" and entry.get("target_id"):
                    disabled.add(str(entry["target_id"]))

        # Pass 2: gather enabled text entries, skipping disabled ones.
        enabled_lines: list[str] = []
        for source in sources:
            for entry in iter_jsonl(source):
                if entry.get("action") == "disable":
                    continue
                entry_id = str(entry.get("id") or "")
                if not entry_id or entry_id in disabled:
                    continue
                if not entry.get("enabled", True):
                    continue
                text = str(entry.get("text") or "").strip()
                if text:
                    enabled_lines.append(f"- {text}")
        return "\n".join(enabled_lines).strip()

    def list_learned_entries(self, project_root: Path) -> list[dict[str, Any]]:
        project_learned_path = ProjectPaths(home_dir=self._paths.home_dir, project_root=project_root).learned_path
        entries: list[dict[str, Any]] = []
        for source_name, source in (("global", self.learned_path), ("project", project_learned_path)):
            for entry in iter_jsonl(source):
                if isinstance(entry, dict):
                    entries.append({"_source": source_name, **entry})
        return entries

    def disable_learned(self, *, project_root: Path, scope: str, target_id: str, rationale: str) -> str:
        entry_id = f"ld_{time.time_ns()}_{secrets.token_hex(4)}"
        if scope == "project":
            target = ProjectPaths(home_dir=self._paths.home_dir, project_root=project_root).learned_path
        else:
            target = self.learned_path
        append_jsonl(
            target,
            {
                "id": entry_id,
                "ts": now_rfc3339(),
                "action": "disable",
                "target_id": target_id,
                "rationale": rationale,
            },
        )
        return entry_id

    def load_project_overlay(self, project_root: Path) -> dict[str, Any]:
        project_paths = ProjectPaths(home_dir=self._paths.home_dir, project_root=project_root)
        overlay = read_json(project_paths.overlay_path, default=None)
        changed = False
        if overlay is None:
            overlay = {}
            changed = True

        if not isinstance(overlay, dict):
            overlay = {}
            changed = True

        def ensure_key(k: str, v: Any) -> None:
            nonlocal changed
            if k not in overlay:
                overlay[k] = v
                changed = True

        ensure_key("project_id", project_paths.project_id)
        ensure_key("root_path", str(project_root.resolve()))
        ensure_key("stack_hints", [])
        ensure_key(
            "testless_verification_strategy",
            {
                "chosen_once": False,
                "strategy": "",
                "rationale": "",
            },
        )
        ensure_key("host_bindings", [])
        ensure_key("global_workflow_overrides", {})
        ensure_key(
            "hands_state",
            {
                "provider": "",
                "thread_id": "",
                "updated_ts": "",
                },
        )
        ensure_key(
            "workflow_run",
            {
                "version": "v1",
                "active": False,
                "workflow_id": "",
                "workflow_name": "",
                "thread_id": "",
                "started_ts": "",
                "updated_ts": "",
                "completed_step_ids": [],
                "next_step_id": "",
                "last_batch_id": "",
                "last_confidence": 0.0,
                "last_notes": "",
                "close_reason": "",
            },
        )

        # Update derived identity fields (used for stable cross-path resolution).
        ident = project_identity(project_root)
        identity_key = str(ident.get("key") or "").strip()
        ensure_key("identity_key", identity_key)
        ensure_key("identity", ident if isinstance(ident, dict) else {})

        if str(overlay.get("project_id") or "").strip() != project_paths.project_id:
            overlay["project_id"] = project_paths.project_id
            changed = True

        cur_root_path = str(project_root.resolve())
        if str(overlay.get("root_path") or "").strip() != cur_root_path:
            overlay["root_path"] = cur_root_path
            changed = True

        if identity_key and str(overlay.get("identity_key") or "").strip() != identity_key:
            overlay["identity_key"] = identity_key
            changed = True
        if isinstance(overlay.get("identity"), dict):
            if overlay.get("identity") != ident:
                overlay["identity"] = ident
                changed = True
        else:
            overlay["identity"] = ident
            changed = True

        # Patch nested keys for forward-compat.
        hs = overlay.get("hands_state")
        if not isinstance(hs, dict):
            overlay["hands_state"] = {"provider": "", "thread_id": "", "updated_ts": ""}
            changed = True
        else:
            for k, default_v in (("provider", ""), ("thread_id", ""), ("updated_ts", "")):
                if k not in hs:
                    hs[k] = default_v
                    changed = True

        gwo = overlay.get("global_workflow_overrides")
        if not isinstance(gwo, dict):
            overlay["global_workflow_overrides"] = {}
            changed = True

        wr = overlay.get("workflow_run")
        if not isinstance(wr, dict):
            overlay["workflow_run"] = {
                "version": "v1",
                "active": False,
                "workflow_id": "",
                "workflow_name": "",
                "thread_id": "",
                "started_ts": "",
                "updated_ts": "",
                "completed_step_ids": [],
                "next_step_id": "",
                "last_batch_id": "",
                "last_confidence": 0.0,
                "last_notes": "",
                "close_reason": "",
            }
            changed = True
        else:
            for k, default_v in (
                ("version", "v1"),
                ("active", False),
                ("workflow_id", ""),
                ("workflow_name", ""),
                ("thread_id", ""),
                ("started_ts", ""),
                ("updated_ts", ""),
                ("completed_step_ids", []),
                ("next_step_id", ""),
                ("last_batch_id", ""),
                ("last_confidence", 0.0),
                ("last_notes", ""),
                ("close_reason", ""),
            ):
                if k not in wr:
                    wr[k] = default_v
                    changed = True

        if changed:
            write_json(project_paths.overlay_path, overlay)
        return overlay

    def write_project_overlay(self, project_root: Path, overlay: dict[str, Any]) -> None:
        project_paths = ProjectPaths(home_dir=self._paths.home_dir, project_root=project_root)
        write_json(project_paths.overlay_path, overlay)

    def load(self, project_root: Path) -> LoadedMindSpec:
        base = self.load_base()
        learned_text = self.load_learned_text(project_root)
        overlay = self.load_project_overlay(project_root)
        return LoadedMindSpec(base=base, learned_text=learned_text, project_overlay=overlay)
