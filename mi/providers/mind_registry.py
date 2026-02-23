from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict

from ..core.config import resolve_api_key
from .llm import MiLlm
from .mind_anthropic import AnthropicMindProvider
from .mind_openai_compat import OpenAICompatibleMindProvider
from .types import MindProvider


# Use typing.* here (not built-in generics) since this alias is evaluated at import time.
MindProviderFactory = Callable[[Dict[str, Any], Path, Path], MindProvider]


def _build_codex_schema(cfg: dict[str, Any], project_root: Path, transcripts_dir: Path) -> MindProvider:
    # V1 default: call Codex itself with a JSON schema and parse the JSON from the response.
    return MiLlm(project_root=project_root, transcripts_dir=transcripts_dir)


def _build_openai_compatible(cfg: dict[str, Any], _project_root: Path, transcripts_dir: Path) -> MindProvider:
    mind = cfg.get("mind") if isinstance(cfg.get("mind"), dict) else {}
    oc = mind.get("openai_compatible") if isinstance(mind.get("openai_compatible"), dict) else {}
    api_key = resolve_api_key(oc if isinstance(oc, dict) else {})
    return OpenAICompatibleMindProvider(
        base_url=str(oc.get("base_url") or "").strip() or "https://api.openai.com/v1",
        model=str(oc.get("model") or "").strip(),
        api_key=api_key,
        transcripts_dir=transcripts_dir,
        timeout_s=int(oc.get("timeout_s") or 60),
        max_retries=int(oc.get("max_retries") or 2),
    )


def _build_anthropic(cfg: dict[str, Any], _project_root: Path, transcripts_dir: Path) -> MindProvider:
    mind = cfg.get("mind") if isinstance(cfg.get("mind"), dict) else {}
    ac = mind.get("anthropic") if isinstance(mind.get("anthropic"), dict) else {}
    api_key = resolve_api_key(ac if isinstance(ac, dict) else {})
    return AnthropicMindProvider(
        base_url=str(ac.get("base_url") or "").strip() or "https://api.anthropic.com",
        model=str(ac.get("model") or "").strip(),
        api_key=api_key,
        transcripts_dir=transcripts_dir,
        timeout_s=int(ac.get("timeout_s") or 60),
        max_retries=int(ac.get("max_retries") or 2),
        anthropic_version=str(ac.get("anthropic_version") or "2023-06-01").strip(),
        max_tokens=int(ac.get("max_tokens") or 2048),
    )


_MIND_FACTORIES: Dict[str, MindProviderFactory] = {
    "codex_schema": _build_codex_schema,
    "openai_compatible": _build_openai_compatible,
    "anthropic": _build_anthropic,
}


def mind_provider_names() -> list[str]:
    return sorted(_MIND_FACTORIES.keys())


def make_mind_provider(cfg: dict[str, Any], *, project_root: Path, transcripts_dir: Path) -> MindProvider:
    mind = cfg.get("mind") if isinstance(cfg.get("mind"), dict) else {}
    provider = str(mind.get("provider") or "codex_schema").strip()
    fn = _MIND_FACTORIES.get(provider)
    if fn is None:
        raise ValueError(f"unknown mind provider: {provider}")
    return fn(cfg, project_root, transcripts_dir)
