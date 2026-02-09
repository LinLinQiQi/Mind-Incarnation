from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .storage import ensure_dir, read_json, write_json


def config_path(home_dir: Path) -> Path:
    return home_dir / "config.json"


def default_config() -> dict[str, Any]:
    # Keep this minimal and editable by hand; values/preferences live in MindSpec.
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
