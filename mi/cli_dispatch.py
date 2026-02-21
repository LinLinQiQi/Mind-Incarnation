from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .cli_commands import (
    handle_show,
    handle_tail,
    handle_knowledge_workflow_host_commands,
    handle_run_memory_gc_commands,
    handle_status_project_commands,
    handle_config_init_values_settings_commands,
    run_values_set_flow,
)
from .core.paths import (
    ProjectPaths,
    resolve_cli_project_root,
    record_last_project_selection,
)
from .thoughtdb import ThoughtDbStore


def _read_stdin_text() -> str:
    data = sys.stdin.read()
    return data.strip("\n")


def _read_user_line(question: str) -> str:
    print(question.strip(), file=sys.stderr)
    print("> ", end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().strip()


def _unified_diff(a: str, b: str, *, fromfile: str, tofile: str, limit_lines: int = 400) -> str:
    diff = list(
        difflib.unified_diff(
            a.splitlines(True),
            b.splitlines(True),
            fromfile=fromfile,
            tofile=tofile,
        )
    )
    if len(diff) > limit_lines:
        diff = diff[:limit_lines] + ["... (diff truncated)\n"]
    return "".join(diff).rstrip() + "\n" if diff else ""


def _effective_cd_arg(args: argparse.Namespace) -> str:
    """Return the effective project selection argument for project-scoped commands.

    Precedence: subcommand --cd (if any) overrides global -C/--cd.
    """

    cd = str(getattr(args, "cd", "") or "").strip()
    if cd:
        return cd
    return str(getattr(args, "global_cd", "") or "").strip()


def _resolve_project_root_from_args(home_dir: Path, cd_arg: str, *, cfg: dict[str, Any] | None = None, here: bool = False) -> Path:
    """Resolve an effective project root for CLI handlers.

    - If `--cd` is omitted, MI may infer git toplevel (see `resolve_cli_project_root`).
    - Print a short stderr note when inference changes the root away from cwd.
    """

    root, reason = resolve_cli_project_root(home_dir, cd_arg, cwd=Path.cwd(), here=bool(here))
    if str(reason or "").startswith("error:alias_missing:"):
        token = str(reason).split("error:alias_missing:", 1)[-1].strip() or str(cd_arg or "").strip()
        print(f"[mi] unknown project token: {token}", file=sys.stderr)
        print("[mi] tip: run `mi project alias list` or set `mi project use --cd <path>` to set @last.", file=sys.stderr)
        raise SystemExit(2)
    cwd = Path.cwd().resolve()
    if not str(reason or "").startswith("arg") and root != cwd:
        print(f"[mi] using inferred project_root={root} (reason={reason}, cwd={cwd})", file=sys.stderr)

    # Auto-update the last-used project (non-canonical convenience) to reduce `--cd` burden.
    runtime_cfg = cfg.get("runtime") if isinstance(cfg, dict) and isinstance(cfg.get("runtime"), dict) else {}
    ps_cfg = runtime_cfg.get("project_selection") if isinstance(runtime_cfg.get("project_selection"), dict) else {}
    auto_update = bool(ps_cfg.get("auto_update_last", True))
    if auto_update:
        try:
            record_last_project_selection(home_dir, root)
        except Exception:
            pass
    return root


def dispatch(*, args: argparse.Namespace, home_dir: Path, cfg: dict[str, Any]) -> int:
    def _make_global_tdb() -> ThoughtDbStore:
        # Use a dummy ProjectPaths id to avoid accidentally creating a project mapping during global operations.
        dummy_pp = ProjectPaths(home_dir=home_dir, project_root=Path("."), _project_id="__global__")
        return ThoughtDbStore(home_dir=home_dir, project_paths=dummy_pp)

    def _do_values_set(
        *,
        values_text: str,
        no_compile: bool,
        no_values_claims: bool,
        show: bool,
        dry_run: bool,
        notes: str,
    ) -> dict[str, Any]:
        return run_values_set_flow(
            home_dir=home_dir,
            cfg=cfg,
            make_global_tdb=_make_global_tdb,
            values_text=values_text,
            no_compile=no_compile,
            no_values_claims=no_values_claims,
            show=show,
            dry_run=dry_run,
            notes=notes,
        )

    def _handle_version_cmd() -> int:
        print(__version__)
        return 0

    def _handle_show_cmd() -> int:
        return handle_show(
            args=args,
            home_dir=home_dir,
            cfg=cfg,
            resolve_project_root_from_args=_resolve_project_root_from_args,
            effective_cd_arg=_effective_cd_arg,
            dispatch_fn=lambda args2, home2, cfg2: dispatch(args=args2, home_dir=home2, cfg=cfg2),
        )

    def _handle_tail_cmd() -> int:
        return handle_tail(
            args=args,
            home_dir=home_dir,
            cfg=cfg,
            resolve_project_root_from_args=_resolve_project_root_from_args,
            effective_cd_arg=_effective_cd_arg,
        )

    simple_handlers = {
        "version": _handle_version_cmd,
        "show": _handle_show_cmd,
        "tail": _handle_tail_cmd,
    }
    simple = simple_handlers.get(str(getattr(args, "cmd", "") or "").strip())
    if callable(simple):
        return int(simple())

    rc_status_project = handle_status_project_commands(
        args=args,
        home_dir=home_dir,
        cfg=cfg,
        make_global_tdb=_make_global_tdb,
        resolve_project_root_from_args=_resolve_project_root_from_args,
        effective_cd_arg=_effective_cd_arg,
    )
    if rc_status_project is not None:
        return rc_status_project

    rc_cfg_values = handle_config_init_values_settings_commands(
        args=args,
        home_dir=home_dir,
        cfg=cfg,
        read_stdin_text=_read_stdin_text,
        do_values_set=_do_values_set,
        make_global_tdb=_make_global_tdb,
        resolve_project_root_from_args=_resolve_project_root_from_args,
        effective_cd_arg=_effective_cd_arg,
    )
    if rc_cfg_values is not None:
        return rc_cfg_values

    rc_runtime = handle_run_memory_gc_commands(
        args=args,
        home_dir=home_dir,
        cfg=cfg,
        resolve_project_root_from_args=_resolve_project_root_from_args,
        effective_cd_arg=_effective_cd_arg,
    )
    if rc_runtime is not None:
        return rc_runtime

    rc = handle_knowledge_workflow_host_commands(
        args=args,
        home_dir=home_dir,
        cfg=cfg,
        resolve_project_root_from_args=_resolve_project_root_from_args,
        effective_cd_arg=_effective_cd_arg,
        read_user_line=_read_user_line,
        unified_diff=_unified_diff,
    )
    if rc is not None:
        return rc

    return 2
