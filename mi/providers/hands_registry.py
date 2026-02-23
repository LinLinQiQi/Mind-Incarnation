from __future__ import annotations

import functools
from typing import Any, Callable, Optional, Tuple, cast

from .codex_runner import run_codex_exec, run_codex_resume
from .hands_cli import CliHandsAdapter
from .types import HandsExecFn, HandsResumeFn


# Use typing.* here (not built-in generics / PEP604) since this alias is evaluated at import time.
HandsProviderFactory = Callable[..., Tuple[HandsExecFn, Optional[HandsResumeFn]]]


def _build_codex(
    cfg: dict[str, Any],
    *,
    live: bool,
    hands_raw: bool,
    redact: bool,
    on_live_line: Callable[[str], None] | None,
) -> tuple[HandsExecFn, HandsResumeFn | None]:
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
    # `functools.partial` has imprecise typing; the runtime contract is enforced by our Hands* Protocols.
    return cast(HandsExecFn, exec_fn), cast(HandsResumeFn, resume_fn)


def _build_cli(
    cfg: dict[str, Any],
    *,
    live: bool,
    hands_raw: bool,
    redact: bool,
    on_live_line: Callable[[str], None] | None,
) -> tuple[HandsExecFn, HandsResumeFn | None]:
    hands = cfg.get("hands") if isinstance(cfg.get("hands"), dict) else {}
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
    return cast(HandsExecFn, exec_fn), (cast(HandsResumeFn, resume_fn) if resume_fn is not None else None)


_HANDS_FACTORIES: dict[str, HandsProviderFactory] = {
    "codex": _build_codex,
    "cli": _build_cli,
}


def hands_provider_names() -> list[str]:
    return sorted(_HANDS_FACTORIES.keys())


def make_hands_functions(
    cfg: dict[str, Any],
    *,
    live: bool = False,
    hands_raw: bool = False,
    redact: bool = False,
    on_live_line: Callable[[str], None] | None = None,
) -> tuple[HandsExecFn, HandsResumeFn | None]:
    hands = cfg.get("hands") if isinstance(cfg.get("hands"), dict) else {}
    provider = str(hands.get("provider") or "codex").strip()
    fn = _HANDS_FACTORIES.get(provider)
    if fn is None:
        raise ValueError(f"unknown hands provider: {provider}")
    return fn(
        cfg,
        live=bool(live),
        hands_raw=bool(hands_raw),
        redact=bool(redact),
        on_live_line=on_live_line,
    )
