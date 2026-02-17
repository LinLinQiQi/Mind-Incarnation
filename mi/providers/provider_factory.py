from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..core.config import resolve_api_key
from .llm import MiLlm
from .mind_anthropic import AnthropicMindProvider
from .mind_openai_compat import OpenAICompatibleMindProvider
from .hands_cli import CliHandsAdapter
from .codex_runner import run_codex_exec, run_codex_resume


def make_mind_provider(cfg: dict[str, Any], *, project_root: Path, transcripts_dir: Path) -> Any:
    mind = cfg.get("mind") if isinstance(cfg.get("mind"), dict) else {}
    provider = str(mind.get("provider") or "codex_schema").strip()

    if provider == "codex_schema":
        return MiLlm(project_root=project_root, transcripts_dir=transcripts_dir)

    if provider == "openai_compatible":
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

    if provider == "anthropic":
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

    raise ValueError(f"unknown mind provider: {provider}")


def make_hands_functions(cfg: dict[str, Any]) -> tuple[Callable[..., Any], Callable[..., Any] | None]:
    hands = cfg.get("hands") if isinstance(cfg.get("hands"), dict) else {}
    provider = str(hands.get("provider") or "codex").strip()

    if provider == "codex":
        return run_codex_exec, run_codex_resume

    if provider == "cli":
        cc = hands.get("cli") if isinstance(hands.get("cli"), dict) else {}
        adapter = CliHandsAdapter(
            exec_argv=list(cc.get("exec") or []),
            resume_argv=list(cc.get("resume") or []) if cc.get("resume") else None,
            prompt_mode=str(cc.get("prompt_mode") or "stdin"),
            env=cc.get("env") if isinstance(cc.get("env"), dict) else {},
            thread_id_regex=str(cc.get("thread_id_regex") or ""),
        )
        return adapter.exec, adapter.resume if adapter.supports_resume else None

    raise ValueError(f"unknown hands provider: {provider}")
