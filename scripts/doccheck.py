#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
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


def _empty_tree_hash() -> str:
    # Stable "empty tree" hash used by git for diffing an initial commit.
    return "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


def _read_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _is_all_zeros_sha(s: str) -> bool:
    xs = str(s or "").strip().lower()
    return bool(xs) and set(xs) == {"0"}


def _ci_diff_args() -> list[str]:
    """Best-effort diff args for CI (GitHub Actions).

    Returns arguments to append after: `git diff --name-only`.
    """

    if not _truthy_env("GITHUB_ACTIONS"):
        return []

    event_name = str(os.environ.get("GITHUB_EVENT_NAME") or "").strip()
    event_path = str(os.environ.get("GITHUB_EVENT_PATH") or "").strip()
    if not event_name or not event_path or not os.path.exists(event_path):
        return []

    ev = _read_json(event_path)

    if event_name == "pull_request":
        pr = ev.get("pull_request") if isinstance(ev.get("pull_request"), dict) else {}
        base = pr.get("base") if isinstance(pr.get("base"), dict) else {}
        head = pr.get("head") if isinstance(pr.get("head"), dict) else {}
        base_sha = str(base.get("sha") or "").strip()
        head_sha = str(head.get("sha") or "").strip() or str(os.environ.get("GITHUB_SHA") or "").strip()
        if base_sha and head_sha:
            # Use merge-base diff for PRs.
            return [f"{base_sha}...{head_sha}"]
        return []

    if event_name == "push":
        before = str(ev.get("before") or "").strip()
        after = str(ev.get("after") or "").strip() or str(os.environ.get("GITHUB_SHA") or "").strip()
        if not after:
            return []
        if before and not _is_all_zeros_sha(before):
            return [f"{before}..{after}"]
        # Branch created or missing "before": diff against empty tree.
        return [_empty_tree_hash(), after]

    return []


def _changed_files_from_worktree() -> set[str]:
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
    return changed


def _changed_files_from_diff_args(diff_args: list[str]) -> set[str]:
    if not diff_args:
        return set()
    rc, out = _run_git(["diff", "--name-only", *diff_args])
    if rc != 0:
        return set()
    return {line.strip() for line in (out or "").splitlines() if line.strip()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Best-effort doc drift checker for MindIncarnation.")
    ap.add_argument(
        "--diff",
        default="",
        help="Check a git diff range instead of the working tree (example: BASE..HEAD or BASE...HEAD).",
    )
    ap.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: infer a diff range from GitHub Actions event metadata (best-effort).",
    )
    args = ap.parse_args()

    rc, top = _run_git(["rev-parse", "--show-toplevel"])
    if rc != 0 or not top:
        # Not a git repo; doccheck is a best-effort developer aid.
        print("[doccheck] not a git repo; skipping", file=sys.stderr)
        return 0

    changed: set[str] = set()
    diff_s = str(getattr(args, "diff", "") or "").strip()
    if diff_s:
        changed = _changed_files_from_diff_args([diff_s])
        mode = f"diff:{diff_s}"
    elif bool(getattr(args, "ci", False)):
        diff_args = _ci_diff_args()
        changed = _changed_files_from_diff_args(diff_args)
        mode = "ci"
    else:
        changed = _changed_files_from_worktree()
        mode = "worktree"

    if not changed:
        print(f"[doccheck] clean (mode={mode})", file=sys.stderr)
        return 0

    strict = _truthy_env("MI_DOCCHECK_STRICT")
    warnings: list[str] = []

    def any_changed(prefixes: tuple[str, ...]) -> bool:
        return any(any(f == p or f.startswith(p + "/") for p in prefixes) for f in changed)

    spec = "docs/mi-v1-spec.md"
    spec_changed = spec in changed

    readme_en = "README.md"
    readme_zh = "README.zh-CN.md"
    readme_changed = any(f in changed for f in (readme_en, readme_zh))

    thoughtdb_doc = "docs/mi-thought-db.md"
    thoughtdb_doc_changed = thoughtdb_doc in changed

    # Spec is source-of-truth for V1 behavior.
    code_changed = any_changed(("mi",)) or any(f in changed for f in ("pyproject.toml", "Makefile"))
    if code_changed and not spec_changed:
        warnings.append(f"Code changed but {spec} not changed (spec is source-of-truth).")

    # CLI / inspection surface typically needs README updates.
    cli_changed = any(f in changed for f in ("mi/cli.py", "mi/cli_dispatch.py", "mi/runtime/inspect.py"))
    cli_changed = cli_changed or any_changed(("mi/schemas",))
    if cli_changed and not readme_changed:
        warnings.append(f"CLI/inspect/schemas changed but README not updated ({readme_en} / {readme_zh}).")

    # Thought DB / memory design notes.
    tdb_changed = any_changed(("mi/thoughtdb", "mi/memory"))
    if tdb_changed and not thoughtdb_doc_changed:
        warnings.append(f"Thought DB / memory changed but {thoughtdb_doc} not updated.")

    # Workflows / host adapters are user-facing; README should usually mention new/changed knobs.
    wf_changed = any_changed(("mi/workflows",))
    hosts_changed = any(f in changed for f in ("mi/workflows/hosts.py",))
    if (wf_changed or hosts_changed) and not readme_changed:
        warnings.append(f"Workflows/host adapters changed but README not updated ({readme_en} / {readme_zh}).")

    # Provider config/templates are user-facing.
    providers_changed = any_changed(("mi/providers",)) or any(f in changed for f in ("mi/core/config.py",))
    if providers_changed and not readme_changed:
        warnings.append(f"Provider config changed but README not updated ({readme_en} / {readme_zh}).")

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
