#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime
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

    Returns arguments to append after: `git diff --name-status`.
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
    changed, _, _ = _changes_from_worktree()
    return changed


def _changed_files_from_diff_args(diff_args: list[str]) -> set[str]:
    changed, _, _ = _changes_from_diff_args(diff_args)
    return changed


def _parse_name_status(out: str) -> tuple[set[str], set[str], set[str]]:
    """Parse `git diff --name-status` output into (changed, added, deleted)."""

    changed: set[str] = set()
    added: set[str] = set()
    deleted: set[str] = set()

    for raw in (out or "").splitlines():
        line = raw.strip()
        if not line:
            continue

        # Git uses tabs by default; fall back to whitespace if needed.
        parts = line.split("\t") if "\t" in line else line.split()
        if not parts:
            continue

        status = parts[0].strip()
        if not status:
            continue

        code = status[0].upper()

        # Rename/copy have: Rxxx old new / Cxxx old new.
        if code in ("R", "C") and len(parts) >= 3:
            old = parts[1].strip()
            new = parts[2].strip()
            if old:
                changed.add(old)
                deleted.add(old)
            if new:
                changed.add(new)
                added.add(new)
            continue

        path = parts[1].strip() if len(parts) >= 2 else ""
        if not path:
            continue

        changed.add(path)
        if code == "A":
            added.add(path)
        elif code == "D":
            deleted.add(path)

    return changed, added, deleted


def _changes_from_worktree() -> tuple[set[str], set[str], set[str]]:
    changed: set[str] = set()
    added: set[str] = set()
    deleted: set[str] = set()

    # Unstaged and staged diffs.
    for cmd in (["diff", "--name-status"], ["diff", "--name-status", "--cached"]):
        _, out = _run_git(cmd)
        ch, ad, de = _parse_name_status(out)
        changed |= ch
        added |= ad
        deleted |= de

    # Untracked files: treat as added.
    _, out = _run_git(["ls-files", "--others", "--exclude-standard"])
    for line in (out or "").splitlines():
        p = line.strip()
        if not p:
            continue
        changed.add(p)
        added.add(p)

    return changed, added, deleted


def _changes_from_diff_args(diff_args: list[str]) -> tuple[set[str], set[str], set[str]]:
    if not diff_args:
        return set(), set(), set()
    rc, out = _run_git(["diff", "--name-status", *diff_args])
    if rc != 0:
        return set(), set(), set()
    return _parse_name_status(out)


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
    added: set[str] = set()
    deleted: set[str] = set()
    diff_s = str(getattr(args, "diff", "") or "").strip()
    if diff_s:
        changed, added, deleted = _changes_from_diff_args([diff_s])
        mode = f"diff:{diff_s}"
    elif bool(getattr(args, "ci", False)):
        diff_args = _ci_diff_args()
        changed, added, deleted = _changes_from_diff_args(diff_args)
        mode = "ci"
    else:
        changed, added, deleted = _changes_from_worktree()
        mode = "worktree"

    if not changed:
        print(f"[doccheck] clean (mode={mode})", file=sys.stderr)
        return 0

    strict = _truthy_env("MI_DOCCHECK_STRICT")
    warnings: list[tuple[str, str, tuple[str, ...]]] = []
    expected_docs: set[str] = set()

    def warn(*, category: str, message: str, expected: tuple[str, ...] = ()) -> None:
        warnings.append((str(category or "").strip() or "other", str(message or "").strip(), tuple(expected or ())))
        for p in expected or ():
            ps = str(p or "").strip()
            if ps:
                expected_docs.add(ps)

    def any_changed(prefixes: tuple[str, ...]) -> bool:
        return any(any(f == p or f.startswith(p + "/") for p in prefixes) for f in changed)

    spec = "docs/mi-v1-spec.md"
    spec_changed = spec in changed

    readme_en = "README.md"
    readme_zh = "README.zh-CN.md"
    readme_en_changed = readme_en in changed
    readme_zh_changed = readme_zh in changed
    readme_any_changed = readme_en_changed or readme_zh_changed

    thoughtdb_doc = "docs/mi-thought-db.md"
    thoughtdb_doc_changed = thoughtdb_doc in changed
    doc_map = "references/doc-map.md"
    doc_map_changed = doc_map in changed

    # Spec is source-of-truth for V1 behavior.
    code_changed = any_changed(("mi",)) or any(f in changed for f in ("pyproject.toml", "Makefile"))
    if code_changed and not spec_changed:
        warn(
            category="spec",
            message=f"Code changed but {spec} not changed (spec is source-of-truth).",
            expected=(spec,),
        )

    # README updates are usually required for user-facing surface changes.
    readme_reasons: list[str] = []
    cli_changed = any(f in changed for f in ("mi/cli.py", "mi/cli_dispatch.py", "mi/runtime/inspect.py")) or any_changed(("mi/schemas",))
    if cli_changed:
        readme_reasons.append("CLI/inspect/schemas")

    # Thought DB / memory design notes.
    tdb_changed = any_changed(("mi/thoughtdb", "mi/memory"))
    if tdb_changed and not thoughtdb_doc_changed:
        warn(
            category="thoughtdb_doc",
            message=f"Thought DB / memory changed but {thoughtdb_doc} not updated.",
            expected=(thoughtdb_doc,),
        )

    # Workflows / host adapters are user-facing; README should usually mention new/changed knobs.
    wf_changed = any_changed(("mi/workflows",)) or any(f in changed for f in ("mi/workflows/hosts.py",))
    if wf_changed:
        readme_reasons.append("Workflows/host adapters")

    # Provider config/templates are user-facing.
    providers_changed = any_changed(("mi/providers",)) or any(f in changed for f in ("mi/core/config.py",))
    if providers_changed:
        readme_reasons.append("Providers/config")

    if readme_reasons:
        reasons_s = ", ".join(readme_reasons)
        if not readme_any_changed:
            warn(
                category="readme",
                message=f"{reasons_s} changed but README not updated.",
                expected=(readme_en, readme_zh),
            )
        elif readme_en_changed != readme_zh_changed:
            warn(
                category="readme_sync",
                message="README updated in only one language; keep README.md and README.zh-CN.md in sync.",
                expected=(readme_en, readme_zh),
            )

    # If the spec itself changed, ensure the header date is meaningful.
    if spec_changed and spec not in deleted:
        today = datetime.date.today().isoformat()
        last_updated = ""
        try:
            with open(spec, "r", encoding="utf-8") as f:
                for _ in range(80):
                    line = f.readline()
                    if not line:
                        break
                    if line.lower().startswith("last updated:"):
                        last_updated = line.split(":", 1)[1].strip()
                        break
        except Exception:
            last_updated = ""
        if not last_updated:
            warn(
                category="spec_date",
                message=f"{spec} changed but missing 'Last updated: YYYY-MM-DD' line.",
                expected=(spec,),
            )
        elif last_updated != today:
            warn(
                category="spec_date",
                message=f"{spec} changed but 'Last updated' is {last_updated} (expected {today}).",
                expected=(spec,),
            )

    # If a new docs/* file is added, require updating doc-map so future changes stay honest.
    excluded_docs = {spec, thoughtdb_doc}
    new_docs = sorted([p for p in added if p.startswith("docs/") and p not in excluded_docs])
    if new_docs and not doc_map_changed:
        sample = ", ".join(new_docs[:3])
        more = "" if len(new_docs) <= 3 else f" (+{len(new_docs) - 3} more)"
        warn(
            category="doc_map",
            message=f"New docs added ({sample}{more}) but {doc_map} not updated.",
            expected=(doc_map,),
        )

    if warnings:
        for cat, msg, exp in warnings[:40]:
            exp_s = ", ".join([x for x in exp if str(x).strip()])
            suffix = f" -> expected: {exp_s}" if exp_s else ""
            print(f"[doccheck] WARN: {cat}: {msg}{suffix}", file=sys.stderr)
        if expected_docs:
            docs_s = ", ".join(sorted(expected_docs))
            print(f"[doccheck] Docs to review/update: {docs_s}", file=sys.stderr)
        if strict:
            print("[doccheck] strict mode: failing due to warnings", file=sys.stderr)
            return 1
        print("[doccheck] warnings present (non-strict): ok", file=sys.stderr)
        return 0

    print("[doccheck] ok", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
