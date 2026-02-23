from __future__ import annotations

from pathlib import Path

from .cli_parser import build_parser
from .core.config import load_config
from .core.paths import default_home_dir


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    home_dir = Path(str(args.home)).expanduser().resolve() if args.home else default_home_dir()
    cfg = load_config(home_dir)

    from .cli_dispatch import dispatch

    return dispatch(args=args, home_dir=home_dir, cfg=cfg)
