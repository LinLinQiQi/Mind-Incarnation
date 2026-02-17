#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys


def _run_git(args: list[str]) -> tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return 1, ""
    out = (p.stdout or "").strip()
    return p.returncode, out


def _truthy_env(name: str) -> bool:
    v = str(os.environ.get(name) or "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def main() -> int:
    rc, top = _run_git(["rev-parse", "--show-toplevel"])
    if rc != 0 or not top:
        # Not a git repo; doccheck is a best-effort developer aid.
        print("[doccheck] not a git repo; skipping", file=sys.stderr)
        return 0

    changed: set[str] = set()
    for cmd in (
        ["diff", "--name-only"],
        ["diff", "--name-only", "--cached"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        _, out = _run_git(cmd)
        for line in (out or "").splitlines():
            p = line.strip()
            if p:
                changed.add(p)

    if not changed:
        print("[doccheck] clean", file=sys.stderr)
        return 0

    strict = _truthy_env("MI_DOCCHECK_STRICT")
    warnings: list[str] = []

    def any_changed(prefixes: tuple[str, ...]) -> bool:
        return any(any(f == p or f.startswith(p + "/") for p in prefixes) for f in changed)

    spec = "docs/mi-v1-spec.md"
    spec_changed = spec in changed

    code_changed = any_changed(("mi",)) or any(f in changed for f in ("pyproject.toml", "Makefile"))
    if code_changed and not spec_changed:
        warnings.append(f"Code changed but {spec} not changed (spec is source-of-truth).")

    cli_changed = any(f in changed for f in ("mi/cli.py", "mi/cli_dispatch.py")) or any_changed(("mi/runtime/inspect.py",))
    readme_changed = any(f in changed for f in ("README.md", "README.zh-CN.md"))
    if cli_changed and not readme_changed:
        warnings.append("CLI/inspect surface changed but README not updated (README.md / README.zh-CN.md).")

    if warnings:
        for w in warnings[:20]:
            print(f"[doccheck] WARN: {w}", file=sys.stderr)
        if strict:
            print("[doccheck] strict mode: failing due to warnings", file=sys.stderr)
            return 1
        print("[doccheck] warnings present (non-strict): ok", file=sys.stderr)
        return 0

    print("[doccheck] ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

