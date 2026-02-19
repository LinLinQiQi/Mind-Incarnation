from __future__ import annotations

import functools
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


def make_hands_functions(
    cfg: dict[str, Any],
    *,
    live: bool = False,
    hands_raw: bool = False,
    redact: bool = False,
    on_live_line: Callable[[str], None] | None = None,
) -> tuple[Callable[..., Any], Callable[..., Any] | None]:
    hands = cfg.get("hands") if isinstance(cfg.get("hands"), dict) else {}
    provider = str(hands.get("provider") or "codex").strip()

    if provider == "codex":
        exec_fn = functools.partial(
            run_codex_exec,
            live=bool(live),
            hands_raw=bool(hands_raw),
            redact=bool(redact),
            on_live_line=on_live_line,
        )
        resume_fn = functools.partial(
            run_codex_resume,
            live=bool(live),
            hands_raw=bool(hands_raw),
            redact=bool(redact),
            on_live_line=on_live_line,
        )
        return exec_fn, resume_fn

    if provider == "cli":
        cc = hands.get("cli") if isinstance(hands.get("cli"), dict) else {}
        adapter = CliHandsAdapter(
            exec_argv=list(cc.get("exec") or []),
            resume_argv=list(cc.get("resume") or []) if cc.get("resume") else None,
            prompt_mode=str(cc.get("prompt_mode") or "stdin"),
            env=cc.get("env") if isinstance(cc.get("env"), dict) else {},
            thread_id_regex=str(cc.get("thread_id_regex") or ""),
        )
        exec_fn = functools.partial(
            adapter.exec,
            live=bool(live),
            hands_raw=bool(hands_raw),
            redact=bool(redact),
            on_live_line=on_live_line,
        )
        resume_fn = (
            functools.partial(
                adapter.resume,
                live=bool(live),
                hands_raw=bool(hands_raw),
                redact=bool(redact),
                on_live_line=on_live_line,
            )
            if adapter.supports_resume
            else None
        )
        return exec_fn, resume_fn

    raise ValueError(f"unknown hands provider: {provider}")
