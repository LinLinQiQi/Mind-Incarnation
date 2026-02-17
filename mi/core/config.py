from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from .storage import ensure_dir, read_json, write_json, now_rfc3339


def config_path(home_dir: Path) -> Path:
    return home_dir / "config.json"


def default_config() -> dict[str, Any]:
    # Keep this editable by hand.
    #
    # Canonical values/preferences live in Thought DB (claims/nodes).
    # Runtime "knobs" (budgets, feature switches) live here under `runtime`.
    return {
        "version": "v1",
        "mind": {
            # V1 default: use Codex CLI with --output-schema for strict JSON.
            "provider": "codex_schema",  # codex_schema|openai_compatible|anthropic
            "openai_compatible": {
                "base_url": "https://api.openai.com/v1",
                "model": "",
                # Prefer env; allow api_key in file as a last resort.
                "api_key_env": "OPENAI_API_KEY",
                "api_key": "",
                "timeout_s": 60,
                "max_retries": 2,
            },
            "anthropic": {
                "base_url": "https://api.anthropic.com",
                "model": "",
                "api_key_env": "ANTHROPIC_API_KEY",
                "api_key": "",
                "timeout_s": 60,
                "max_retries": 2,
                # Required by Anthropic API; allow override to match vendor changes.
                "anthropic_version": "2023-06-01",
                "max_tokens": 2048,
            },
        },
        "hands": {
            # V1 default: Codex CLI as Hands.
            "provider": "codex",  # codex|cli
            # When true, MI will try to reuse the last stored Hands session/thread id across separate `mi run` invocations.
            # (This is still best-effort; resume may fail if the underlying tool cannot resume by id.)
            "continue_across_runs": False,
            "cli": {
                # exec/resume argv are lists of strings. Placeholders supported:
                # - {project_root}
                # - {thread_id} (resume only)
                # If prompt_mode="arg", include a "{prompt}" placeholder in argv.
                "exec": [],
                "resume": [],
                "prompt_mode": "stdin",  # stdin|arg
                # Optional: extract a thread id from stdout/stderr (first match wins).
                # Must contain a capturing group for the id.
                "thread_id_regex": "",
                "env": {},
            },
        },
        "runtime": {
            "project_selection": {
                # When true, project-scoped CLI commands update `global/project_selection.json`
                # so `mi run ...` can work from any directory (outside of git).
                "auto_update_last": True,
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
                "auto_mine": True,
                "auto_enable": True,
                "min_occurrences": 2,
                "allow_single_if_high_benefit": True,
                "auto_sync_on_change": True,
            },
            "cross_project_recall": {
                "enabled": True,
                "top_k": 3,
                "max_chars": 1800,
                "include_kinds": ["snapshot", "workflow", "claim", "node"],
                # Default: recall within current project is allowed (and preferred).
                "exclude_current_project": False,
                "prefer_current_project": True,
                "triggers": {
                    "run_start": True,
                    "before_ask_user": True,
                    "risk_signal": True,
                },
            },
            "preference_mining": {
                "auto_mine": True,
                "min_occurrences": 2,
                "allow_single_if_high_benefit": True,
                "min_confidence": 0.75,
                "max_suggestions": 3,
            },
            "thought_db": {
                "enabled": True,
                "auto_mine": True,
                "min_confidence": 0.9,
                "max_claims_per_checkpoint": 6,
                "auto_materialize_nodes": True,
            },
            "violation_response": {
                "auto_learn": True,
                "prompt_user_on_high_risk": True,
                "prompt_user_risk_severities": ["high", "critical"],
                "prompt_user_risk_categories": [],
                "prompt_user_respect_should_ask_user": True,
            },
        },
    }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def load_config(home_dir: Path) -> dict[str, Any]:
    cfg = read_json(config_path(home_dir), default=None)
    if not isinstance(cfg, dict):
        return default_config()
    return _deep_merge(default_config(), cfg)


def init_config(home_dir: Path, *, force: bool) -> Path:
    path = config_path(home_dir)
    if path.exists() and not force:
        return path
    ensure_dir(home_dir)
    write_json(path, default_config())
    return path


def load_config_raw(home_dir: Path) -> dict[str, Any]:
    cfg = read_json(config_path(home_dir), default=None)
    return cfg if isinstance(cfg, dict) else {}


def _backups_dir(home_dir: Path) -> Path:
    return home_dir / "backups"


def _last_backup_marker_path(home_dir: Path) -> Path:
    return _backups_dir(home_dir) / "config.last_backup"


def apply_config_template(home_dir: Path, *, name: str) -> dict[str, str]:
    """Deep-merge a named template into config.json, writing a rollback backup.

    Returns {"config_path": "...", "backup_path": "..."}.
    """

    tmpl = get_config_template(name)

    path = config_path(home_dir)
    raw = load_config_raw(home_dir)

    ts = now_rfc3339().replace(":", "").replace("-", "")
    backups = _backups_dir(home_dir)
    ensure_dir(backups)
    backup_path = backups / f"config.json.{ts}.bak"
    write_json(backup_path, raw)
    _last_backup_marker_path(home_dir).write_text(str(backup_path), encoding="utf-8")

    merged = _deep_merge(raw, tmpl)
    if "version" not in merged:
        merged["version"] = "v1"
    write_json(path, merged)

    return {"config_path": str(path), "backup_path": str(backup_path)}


def rollback_config(home_dir: Path) -> dict[str, str]:
    """Rollback config.json to the last apply-template backup.

    Returns {"config_path": "...", "backup_path": "..."}.
    """

    marker = _last_backup_marker_path(home_dir)
    try:
        backup_s = marker.read_text(encoding="utf-8").strip()
    except FileNotFoundError as e:
        raise FileNotFoundError("no rollback backup found (run `mi config apply-template ...` first)") from e

    backup_path = Path(backup_s).expanduser()
    data = read_json(backup_path, default=None)
    if not isinstance(data, dict):
        raise ValueError(f"backup file is not a JSON object: {backup_path}")

    path = config_path(home_dir)
    write_json(path, data)
    return {"config_path": str(path), "backup_path": str(backup_path)}


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in ("api_key", "apikey", "password", "token", "secret"):
                if isinstance(v, str) and v.strip():
                    out[k] = "[REDACTED]"
                else:
                    out[k] = v
            else:
                out[k] = _redact_obj(v)
        return out
    if isinstance(obj, list):
        return [_redact_obj(x) for x in obj]
    return obj


def config_for_display(cfg: dict[str, Any]) -> dict[str, Any]:
    return _redact_obj(cfg)


def resolve_api_key(provider_cfg: dict[str, Any]) -> str:
    env_name = str(provider_cfg.get("api_key_env") or "").strip()
    if env_name:
        v = os.environ.get(env_name)
        if isinstance(v, str) and v.strip():
            return v.strip()
    key = provider_cfg.get("api_key")
    return str(key or "").strip()


def validate_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Validate MI config.json (best-effort).

    Returns: {"ok": bool, "errors": [...], "warnings": [...]}.
    """

    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(cfg, dict):
        return {"ok": False, "errors": ["config is not an object"], "warnings": []}

    mind = cfg.get("mind") if isinstance(cfg.get("mind"), dict) else {}
    hands = cfg.get("hands") if isinstance(cfg.get("hands"), dict) else {}

    mind_provider = str(mind.get("provider") or "codex_schema").strip()
    hands_provider = str(hands.get("provider") or "codex").strip()

    def need_cmd(cmd: str, *, context: str) -> None:
        if shutil.which(cmd) is None:
            errors.append(f"{context}: command not found in PATH: {cmd!r}")

    # Mind provider validation.
    if mind_provider == "codex_schema":
        need_cmd("codex", context="mind.provider=codex_schema")
    elif mind_provider == "openai_compatible":
        oc = mind.get("openai_compatible") if isinstance(mind.get("openai_compatible"), dict) else {}
        model = str(oc.get("model") or "").strip()
        if not model:
            errors.append("mind.provider=openai_compatible: missing mind.openai_compatible.model")
        api_key = resolve_api_key(oc if isinstance(oc, dict) else {})
        if not api_key:
            env_name = str(oc.get("api_key_env") or "OPENAI_API_KEY").strip()
            errors.append(f"mind.provider=openai_compatible: missing API key (set ${env_name} or mind.openai_compatible.api_key)")
    elif mind_provider == "anthropic":
        ac = mind.get("anthropic") if isinstance(mind.get("anthropic"), dict) else {}
        model = str(ac.get("model") or "").strip()
        if not model:
            errors.append("mind.provider=anthropic: missing mind.anthropic.model")
        api_key = resolve_api_key(ac if isinstance(ac, dict) else {})
        if not api_key:
            env_name = str(ac.get("api_key_env") or "ANTHROPIC_API_KEY").strip()
            errors.append(f"mind.provider=anthropic: missing API key (set ${env_name} or mind.anthropic.api_key)")
    else:
        errors.append(f"mind.provider: unknown provider {mind_provider!r}")

    # Hands provider validation.
    if hands_provider == "codex":
        need_cmd("codex", context="hands.provider=codex")
    elif hands_provider == "cli":
        cc = hands.get("cli") if isinstance(hands.get("cli"), dict) else {}
        exec_argv = list(cc.get("exec") or [])
        if not exec_argv:
            errors.append("hands.provider=cli: missing hands.cli.exec argv list")
        else:
            cmd0 = str(exec_argv[0])
            if shutil.which(cmd0) is None and not Path(cmd0).exists():
                warnings.append(f"hands.provider=cli: command may not exist: {cmd0!r}")

        prompt_mode = str(cc.get("prompt_mode") or "stdin").strip()
        if prompt_mode not in ("stdin", "arg"):
            errors.append(f"hands.provider=cli: invalid hands.cli.prompt_mode={prompt_mode!r} (expected stdin|arg)")
        if prompt_mode == "arg" and exec_argv and not any("{prompt}" in str(x) for x in exec_argv):
            warnings.append("hands.provider=cli: prompt_mode=arg but hands.cli.exec has no {prompt}; MI will append the prompt as a final argv")

        resume_argv = list(cc.get("resume") or [])
        if resume_argv and not any("{thread_id}" in str(x) for x in resume_argv):
            warnings.append("hands.provider=cli: hands.cli.resume is set but has no {thread_id}; resume will not use persisted thread/session id")

        rx = str(cc.get("thread_id_regex") or "").strip()
        if rx:
            try:
                re.compile(rx)
            except Exception as e:
                errors.append(f"hands.provider=cli: invalid hands.cli.thread_id_regex: {e}")

        cont = bool(hands.get("continue_across_runs", False))
        if cont and not resume_argv:
            warnings.append(
                "hands.continue_across_runs=true but hands.cli.resume is empty; MI cannot resume by stored id (consider configuring resume argv or using a CLI continue flag)"
            )
    else:
        errors.append(f"hands.provider: unknown provider {hands_provider!r}")

    ok = not errors
    return {"ok": ok, "errors": errors, "warnings": warnings}


def list_config_templates() -> list[str]:
    # Keep names stable; treat as public CLI surface.
    return [
        "mind.openai_compatible",
        "mind.anthropic",
        "hands.cli.generic",
        "hands.cli.claude_code_placeholder",
    ]


def get_config_template(name: str) -> dict[str, Any]:
    """Return a JSON snippet users can merge into config.json."""

    key = str(name or "").strip()
    if not key:
        raise KeyError("empty template name")

    if key == "mind.openai_compatible":
        return {
            "mind": {
                "provider": "openai_compatible",
                "openai_compatible": {
                    "base_url": "https://api.openai.com/v1",
                    "model": "<model>",
                    "api_key_env": "OPENAI_API_KEY",
                    "api_key": "",
                    "timeout_s": 60,
                    "max_retries": 2,
                },
            }
        }

    if key == "mind.anthropic":
        return {
            "mind": {
                "provider": "anthropic",
                "anthropic": {
                    "base_url": "https://api.anthropic.com",
                    "model": "<model>",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "api_key": "",
                    "timeout_s": 60,
                    "max_retries": 2,
                    "anthropic_version": "2023-06-01",
                    "max_tokens": 2048,
                },
            }
        }

    if key == "hands.cli.generic":
        return {
            "hands": {
                "provider": "cli",
                "continue_across_runs": False,
                "cli": {
                    "prompt_mode": "stdin",
                    "exec": ["<your_agent_cli>", "..."],
                    "resume": [],
                    "thread_id_regex": "",
                    "env": {},
                },
            }
        }

    if key == "hands.cli.claude_code_placeholder":
        return {
            "hands": {
                "provider": "cli",
                "continue_across_runs": False,
                "cli": {
                    "prompt_mode": "arg",
                    # Adjust flags to your local Claude Code version.
                    "exec": ["claude", "...", "{prompt}", "..."],
                    "resume": ["claude", "...", "{thread_id}", "...", "{prompt}", "..."],
                    "thread_id_regex": "\"session_id\"\\s*:\\s*\"([A-Za-z0-9_-]+)\"",
                    "env": {},
                },
            }
        }

    raise KeyError(f"unknown template: {key}")
