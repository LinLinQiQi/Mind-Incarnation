from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Callable

from .claim_ops import handle_claim_commands
from .node_ops import handle_node_commands
from .edge_ops import handle_edge_commands
from .why_ops import handle_why_commands
from .workflow_ops import handle_workflow_commands
from .host_ops import handle_host_commands


def handle_knowledge_workflow_host_commands(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
    read_user_line: Callable[[str], str],
    unified_diff: Callable[..., str],
) -> int | None:
    for handler in (
        handle_claim_commands,
        handle_node_commands,
        handle_edge_commands,
        handle_why_commands,
    ):
        rc = handler(
            args=args,
            home_dir=home_dir,
            cfg=cfg,
            resolve_project_root_from_args=resolve_project_root_from_args,
            effective_cd_arg=effective_cd_arg,
        )
        if rc is not None:
            return rc

    rc = handle_workflow_commands(
        args=args,
        home_dir=home_dir,
        cfg=cfg,
        resolve_project_root_from_args=resolve_project_root_from_args,
        effective_cd_arg=effective_cd_arg,
        read_user_line=read_user_line,
        unified_diff=unified_diff,
    )
    if rc is not None:
        return rc

    rc = handle_host_commands(
        args=args,
        home_dir=home_dir,
        cfg=cfg,
        resolve_project_root_from_args=resolve_project_root_from_args,
        effective_cd_arg=effective_cd_arg,
    )
    if rc is not None:
        return rc

    return None
