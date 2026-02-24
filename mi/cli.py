from __future__ import annotations

import sys
from pathlib import Path

from .cli_parser import build_parser
from .core.config import load_config
from .core.paths import default_home_dir


def _first_positional_index(argv: list[str]) -> int | None:
    """Return index of the first positional token (subcommand slot), or None."""

    i = 0
    while i < len(argv):
        a = str(argv[i] or "")
        if a in ("-h", "--help"):
            return None
        if a == "--":
            return None
        if a in ("--home", "-C", "--cd"):
            i += 2
            continue
        if a.startswith("--home=") or a.startswith("--cd="):
            i += 1
            continue
        if a == "--here":
            i += 1
            continue
        if a.startswith("-"):
            i += 1
            continue
        return i
    return None


def _is_thoughtdb_or_workflow_ref(token: str) -> bool:
    s = str(token or "").strip()
    return s.startswith(("ev_", "cl_", "nd_", "wf_", "ed_"))


def _is_transcript_path_ref(token: str) -> bool:
    s = str(token or "").strip()
    return s.endswith(".jsonl") or s.endswith(".jsonl.gz")


def _rewrite_cli_argv(argv: list[str]) -> list[str]:
    """Allow a lightweight project-selection shorthand.

    UX goal: reduce cognitive load when running MI from arbitrary directories.

    Example:
      mi @pinned status   -> mi -C @pinned status
      mi @repo1 run ...   -> mi -C @repo1 run ...
      mi ~/repo status    -> mi -C ~/repo status
      mi ev_<id>          -> mi show ev_<id>
      mi hands            -> mi tail hands
      mi last             -> mi show last
    """

    args = list(argv or [])
    if not args:
        return ["status"]

    # 1) Project selection shorthand: desugar `@pinned` / `<dir>` into `-C ...`.
    idx = _first_positional_index(args)
    if idx is not None:
        tok = str(args[idx] or "").strip()
        if tok.startswith("@"):
            args = args[:idx] + ["-C", tok] + args[idx + 1 :]
        elif tok[:1] in ("/", ".", "~"):
            # Only treat path-ish tokens as project selection when they already exist
            # and are a directory. This avoids stealing normal command words.
            try:
                p = Path(tok).expanduser()
                if not p.is_absolute():
                    p = (Path.cwd() / p)
                p = p.resolve()
                if p.is_dir():
                    args = args[:idx] + ["-C", str(p)] + args[idx + 1 :]
            except Exception:
                pass

    # 2) Shorthand routes: allow `mi ev_...` / `mi last` / `mi hands`.
    idx2 = _first_positional_index(args)
    if idx2 is not None:
        tok = str(args[idx2] or "").strip()
        if tok in ("last", "@last") or _is_thoughtdb_or_workflow_ref(tok) or _is_transcript_path_ref(tok):
            args = args[:idx2] + ["show", tok] + args[idx2 + 1 :]
        elif tok in ("hands", "mind"):
            args = args[:idx2] + ["tail", tok] + args[idx2 + 1 :]

    # 3) Default command: no subcommand => status (also covers `mi -C @pinned`).
    if _first_positional_index(args) is None:
        return args + ["status"]
    return args


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args(_rewrite_cli_argv(raw_argv))
    home_dir = Path(str(args.home)).expanduser().resolve() if args.home else default_home_dir()
    cfg = load_config(home_dir)

    from .cli_dispatch import dispatch

    return dispatch(args=args, home_dir=home_dir, cfg=cfg)
