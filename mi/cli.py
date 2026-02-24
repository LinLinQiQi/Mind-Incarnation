from __future__ import annotations

import sys
from pathlib import Path

from .cli_parser import build_parser
from .core.config import load_config
from .core.paths import default_home_dir


def _rewrite_cli_argv(argv: list[str]) -> list[str]:
    """Allow a lightweight project-selection shorthand.

    UX goal: reduce cognitive load when running MI from arbitrary directories.

    Example:
      mi @pinned status   -> mi -C @pinned status
      mi @repo1 run ...   -> mi -C @repo1 run ...
      mi ~/repo status    -> mi -C ~/repo status
    """

    args = list(argv or [])
    if not args:
        return ["status"]

    # Find the first positional token (the "subcommand" slot). Argparse expects
    # global options before subcommands, so we desugar `@pinned` into `-C @pinned`.
    i = 0
    while i < len(args):
        a = str(args[i] or "")
        if a in ("-h", "--help"):
            return args
        if a == "--":
            return args

        if a in ("--home", "-C", "--cd"):
            # Consume the value (best-effort).
            i += 2
            continue
        if a.startswith("--home=") or a.startswith("--cd="):
            i += 1
            continue
        if a == "--here":
            i += 1
            continue

        if a.startswith("-"):
            # Unknown global flag; keep scanning (safe: we only rewrite before the first positional).
            i += 1
            continue

        # First positional.
        if a.startswith("@"):
            rewritten = args[:i] + ["-C", a] + args[i + 1 :]
            return rewritten + ["status"] if i == len(args) - 1 else rewritten
        if a[:1] in ("/", ".", "~"):
            # Only treat path-ish tokens as project selection when they already exist
            # and are a directory. This avoids stealing normal command words.
            try:
                p = Path(a).expanduser()
                if not p.is_absolute():
                    p = (Path.cwd() / p)
                p = p.resolve()
                if p.is_dir():
                    rewritten = args[:i] + ["-C", str(p)] + args[i + 1 :]
                    return rewritten + ["status"] if i == len(args) - 1 else rewritten
            except Exception:
                pass
        return args

    # No positional tokens => no subcommand. Default to `status` to reduce friction.
    return args + ["status"]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    args = parser.parse_args(_rewrite_cli_argv(raw_argv))
    home_dir = Path(str(args.home)).expanduser().resolve() if args.home else default_home_dir()
    cfg = load_config(home_dir)

    from .cli_dispatch import dispatch

    return dispatch(args=args, home_dir=home_dir, cfg=cfg)
