from __future__ import annotations

import argparse
import os

from .cli_parsers.general import add_general_subparsers
from .cli_parsers.runtime import add_runtime_subparsers
from .cli_parsers.thoughtdb import add_thoughtdb_subparsers
from .cli_parsers.workflow_host import add_workflow_host_subparsers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mi",
        description="Mind Incarnation (MI) V1: a values-driven mind layer above execution agents (default Hands: Codex CLI).",
    )
    parser.add_argument(
        "--home",
        default=os.environ.get("MI_HOME"),
        help="MI home directory (defaults to $MI_HOME or ~/.mind-incarnation).",
    )
    parser.add_argument(
        "-C",
        "--cd",
        dest="global_cd",
        default="",
        help="Default project root for project-scoped commands (supports @last/@pinned/@alias). Must appear before subcommand; subcommand --cd overrides.",
    )
    parser.add_argument(
        "--here",
        action="store_true",
        help="Force project root to the current working directory (useful for monorepo subdirs). Ignored if --cd/-C is provided. Must appear before subcommand.",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    add_general_subparsers(sub=sub)
    add_runtime_subparsers(sub=sub)
    add_thoughtdb_subparsers(sub=sub)
    add_workflow_host_subparsers(sub=sub)

    return parser


__all__ = ["build_parser"]

