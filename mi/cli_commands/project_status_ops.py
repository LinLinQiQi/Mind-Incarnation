from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Callable

from ..core.config import validate_config
from ..core.paths import (
    ProjectPaths,
    resolve_cli_project_root,
    set_pinned_project_selection,
    clear_pinned_project_selection,
    set_project_alias,
    remove_project_alias,
    list_project_aliases,
    load_project_selection,
    project_selection_path,
    record_last_project_selection,
)
from ..runtime.inspect import load_last_batch_bundle, tail_json_objects
from ..runtime.transcript import last_agent_message_from_transcript
from ..core.redact import redact_text
from ..thoughtdb.values import VALUES_SUMMARY_TAG, existing_values_claims
from ..workflows.hosts import parse_host_bindings
from ..project.overlay_store import load_project_overlay


def handle_status_project_commands(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    make_global_tdb: Callable[[], Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
) -> int | None:
    if args.cmd == "status":
        return _handle_status(
            args=args,
            home_dir=home_dir,
            cfg=cfg,
            make_global_tdb=make_global_tdb,
            effective_cd_arg=effective_cd_arg,
        )

    if args.cmd == "project":
        return _handle_project(
            args=args,
            home_dir=home_dir,
            cfg=cfg,
            resolve_project_root_from_args=resolve_project_root_from_args,
            effective_cd_arg=effective_cd_arg,
        )

    return None


def _handle_status(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    make_global_tdb: Callable[[], Any],
    effective_cd_arg: Callable[[argparse.Namespace], str],
) -> int:
    # Read-only: resolve project root without updating @last.
    cd_arg = effective_cd_arg(args)
    cwd = Path.cwd().resolve()
    root, reason = resolve_cli_project_root(home_dir, cd_arg, cwd=cwd, here=bool(getattr(args, "here", False)))
    if str(reason or "").startswith("error:alias_missing:"):
        token = str(reason).split("error:alias_missing:", 1)[-1].strip() or str(cd_arg or "").strip()
        print(f"[mi] unknown project token: {token}", file=sys.stderr)
        print("[mi] tip: run `mi project alias list` or set `mi project use --cd <path>` to set @last.", file=sys.stderr)
        return 2

    # Config/provider health.
    vcfg = validate_config(cfg)
    hands_cfg = cfg.get("hands") if isinstance(cfg.get("hands"), dict) else {}
    mind_cfg = cfg.get("mind") if isinstance(cfg.get("mind"), dict) else {}
    hands_provider = str(hands_cfg.get("provider") or "codex").strip()
    mind_provider = str(mind_cfg.get("provider") or "codex_schema").strip()

    # Values readiness (canonical: Thought DB global).
    tdb_g = make_global_tdb()
    vals = existing_values_claims(tdb=tdb_g, limit=3)
    values_base_present = bool(vals)

    # Best-effort: detect presence of a values summary node.
    values_summary_present = False
    try:
        v_glob = tdb_g.load_view(scope="global")
        for n in v_glob.iter_nodes(include_inactive=False, include_aliases=False):
            if not isinstance(n, dict):
                continue
            tags = n.get("tags") if isinstance(n.get("tags"), list) else []
            if VALUES_SUMMARY_TAG in {str(x).strip() for x in tags if str(x).strip()}:
                values_summary_present = True
                break
    except Exception:
        values_summary_present = False

    # Project-scoped state (best-effort).
    pp = ProjectPaths(home_dir=home_dir, project_root=root)
    overlay = load_project_overlay(home_dir=home_dir, project_root=root)
    if not isinstance(overlay, dict):
        overlay = {}
    bindings = parse_host_bindings(overlay)

    bundle = load_last_batch_bundle(pp.evidence_log_path)
    hands_input = bundle.get("hands_input") if isinstance(bundle.get("hands_input"), dict) else None
    evidence_item = bundle.get("evidence_item") if isinstance(bundle.get("evidence_item"), dict) else None
    decide_next = bundle.get("decide_next") if isinstance(bundle.get("decide_next"), dict) else None

    transcript_path = ""
    if hands_input and isinstance(hands_input.get("transcript_path"), str):
        transcript_path = hands_input["transcript_path"]
    elif evidence_item and isinstance(evidence_item.get("hands_transcript_ref"), str):
        transcript_path = evidence_item["hands_transcript_ref"]
    last_msg = last_agent_message_from_transcript(Path(transcript_path)) if transcript_path else ""

    # Pending learn suggestions (reduce user burden): show only items that require manual apply.
    pending_suggestions: list[str] = []
    for rec in (bundle.get("learn_suggested") or []) if isinstance(bundle.get("learn_suggested"), list) else []:
        if not isinstance(rec, dict):
            continue
        sid = str(rec.get("id") or "").strip()
        auto = bool(rec.get("auto_learn", True))
        applied_ids = rec.get("applied_claim_ids") if isinstance(rec.get("applied_claim_ids"), list) else []
        if sid and (not auto) and (not applied_ids):
            pending_suggestions.append(sid)
    pending_suggestions = pending_suggestions[:6]

    # Best-effort: detect the latest host_sync record and surface failures.
    host_sync_recent: dict[str, Any] | None = None
    try:
        for obj in reversed(tail_json_objects(pp.evidence_log_path, 200)):
            if isinstance(obj, dict) and str(obj.get("kind") or "").strip() == "host_sync":
                host_sync_recent = obj
                break
    except Exception:
        host_sync_recent = None
    host_sync_ok = True
    if isinstance(host_sync_recent, dict):
        sync_obj = host_sync_recent.get("sync") if isinstance(host_sync_recent.get("sync"), dict) else {}
        host_sync_ok = bool(sync_obj.get("ok", True)) if isinstance(sync_obj, dict) else True

    # Deterministic next-step suggestions (no model calls).
    next_steps: list[str] = []

    def _prefix_project(cmd: str) -> str:
        """Make suggested commands copy/pasteable from any cwd (best-effort)."""

        s = str(cmd or "").strip()
        if not s.startswith("mi "):
            return s
        rest = s[len("mi ") :].lstrip()
        if not rest:
            return s
        # Avoid double-prefixing if the suggestion already includes an explicit selection.
        if rest.startswith(("-C ", "--cd ", "@", "/", ".", "~")):
            return s
        return f"mi {shlex.quote(str(root))} {rest}"

    if not bool(vcfg.get("ok", False)):
        next_steps.append("mi config validate")
    if not values_base_present:
        next_steps.append(_prefix_project('mi values set --text "..."'))
    if pending_suggestions:
        next_steps.append(_prefix_project(f"mi claim apply-suggested {pending_suggestions[0]} --dry-run"))
    st = str(decide_next.get("status") or "") if isinstance(decide_next, dict) else ""
    na = str(decide_next.get("next_action") or "") if isinstance(decide_next, dict) else ""
    if st in ("blocked", "not_done") or na in ("ask_user", "continue", "run_checks"):
        next_steps.append(_prefix_project("mi show last --redact"))
    if bindings and (not host_sync_ok):
        next_steps.append(_prefix_project("mi host sync --json"))
    if not next_steps:
        next_steps.append(_prefix_project('mi run "..."'))
    next_steps = next_steps[:3]

    payload = {
        "cwd": str(cwd),
        "effective_cd": str(cd_arg or ""),
        "here": bool(getattr(args, "here", False)),
        "project_root": str(root),
        "reason": str(reason or ""),
        "config_validate": vcfg,
        "providers": {"hands": hands_provider, "mind": mind_provider},
        "values": {
            "values_base_present": values_base_present,
            "values_summary_present": values_summary_present,
            "values_base_claims_sample": vals,
        },
        "project": {
            "project_id": pp.project_id,
            "project_dir": str(pp.project_dir),
            "evidence_log": str(pp.evidence_log_path),
            "transcripts_dir": str(pp.transcripts_dir),
            "host_bindings": [b.host for b in bindings] if bindings else [],
        },
        "last": {
            "bundle": bundle,
            "hands_last_message": last_msg,
        },
        "pending": {
            "learn_suggested": pending_suggestions,
            "host_sync_ok": host_sync_ok,
            "host_sync_recent": host_sync_recent or {},
        },
        "next_steps": next_steps,
    }

    if bool(getattr(args, "redact", False)):
        if last_msg:
            last_msg = redact_text(last_msg)
        s = json.dumps(payload, indent=2, sort_keys=True)
        payload_s = redact_text(s)
        if bool(getattr(args, "json", False)):
            print(payload_s)
            return 0
        payload2 = json.loads(payload_s)
        payload = payload2 if isinstance(payload2, dict) else payload

    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    print(f"project_root={payload['project_root']} (reason={payload['reason']})")
    print(f"providers: hands={hands_provider} mind={mind_provider}")
    errs = vcfg.get("errors") if isinstance(vcfg.get("errors"), list) else []
    warns = vcfg.get("warnings") if isinstance(vcfg.get("warnings"), list) else []
    print(f"config_ok={str(bool(vcfg.get('ok', False))).lower()} errors={len(errs)} warnings={len(warns)}")
    print(f"values_base_present={str(values_base_present).lower()} values_summary_present={str(values_summary_present).lower()}")
    if last_msg.strip():
        print("")
        print("hands_last_message:")
        print(last_msg.strip())
    if pending_suggestions:
        print("")
        print("pending_learn_suggested:")
        for sid in pending_suggestions[:3]:
            print(f"- {sid}")
    if bindings:
        print("")
        print(f"host_bindings={len(bindings)} host_sync_ok={str(bool(host_sync_ok)).lower()}")
    if next_steps:
        print("")
        print("next_steps:")
        for cmd in next_steps:
            print(f"- {cmd}")
    return 0


def _handle_project(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
) -> int:
    subcmd = str(getattr(args, "project_cmd", "") or "").strip()

    if subcmd == "status":
        cd_arg = effective_cd_arg(args)
        cwd = Path.cwd().resolve()
        root, reason = resolve_cli_project_root(home_dir, cd_arg, cwd=cwd, here=bool(getattr(args, "here", False)))
        if str(reason or "").startswith("error:alias_missing:"):
            token = str(reason).split("error:alias_missing:", 1)[-1].strip() or str(cd_arg or "").strip()
            print(f"[mi] unknown project token: {token}", file=sys.stderr)
            print("[mi] tip: run `mi project alias list` or set `mi project use --cd <path>` to set @last.", file=sys.stderr)
            return 2

        sel = load_project_selection(home_dir)
        payload = {
            "cwd": str(cwd),
            "effective_cd": str(cd_arg or ""),
            "here": bool(getattr(args, "here", False)),
            "project_root": str(root),
            "reason": str(reason or ""),
            "selection_path": str(project_selection_path(home_dir)),
            "selection": sel if isinstance(sel, dict) else {},
        }
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        print(f"project_root={payload['project_root']}")
        print(f"reason={payload['reason']}")
        print(f"cwd={payload['cwd']}")
        if payload["effective_cd"]:
            print(f"cd_arg={payload['effective_cd']}")
        print(f"selection_path={payload['selection_path']}")

        last = sel.get("last") if isinstance(sel, dict) else {}
        pinned = sel.get("pinned") if isinstance(sel, dict) else {}
        aliases = sel.get("aliases") if isinstance(sel, dict) else {}
        last_rp = str(last.get("root_path") or "").strip() if isinstance(last, dict) else ""
        pinned_rp = str(pinned.get("root_path") or "").strip() if isinstance(pinned, dict) else ""
        if pinned_rp:
            print(f"@pinned={pinned_rp}")
        if last_rp:
            print(f"@last={last_rp}")
        if isinstance(aliases, dict) and aliases:
            print(f"aliases={len(aliases)}")
        return 0

    if subcmd == "alias":
        alias_cmd = str(getattr(args, "alias_cmd", "") or "").strip()
        if alias_cmd == "list":
            aliases = list_project_aliases(home_dir)
            payload = {
                "selection_path": str(project_selection_path(home_dir)),
                "aliases": aliases,
            }
            if bool(getattr(args, "json", False)):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            if not aliases:
                print("(no aliases)")
                return 0
            for name in sorted(aliases.keys()):
                entry = aliases.get(name) if isinstance(aliases.get(name), dict) else {}
                rp = str(entry.get("root_path") or "").strip()
                pid = str(entry.get("project_id") or "").strip()
                print(f"- @{name}: {rp} (project_id={pid})" if pid else f"- @{name}: {rp}")
            return 0

        if alias_cmd == "add":
            project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
            name = str(getattr(args, "name", "") or "").strip()
            try:
                entry = set_project_alias(home_dir, name=name, project_root=project_root)
            except Exception as e:
                print(f"alias add failed: {e}", file=sys.stderr)
                return 2
            payload = {"ok": True, "name": name, "entry": entry}
            if bool(getattr(args, "json", False)):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0
            print(f"added @{name} -> {entry.get('root_path')}")
            return 0

        if alias_cmd == "rm":
            name = str(getattr(args, "name", "") or "").strip()
            ok = False
            try:
                ok = remove_project_alias(home_dir, name=name)
            except Exception:
                ok = False
            payload = {"ok": bool(ok), "name": name}
            if bool(getattr(args, "json", False)):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0 if ok else 2
            if ok:
                print(f"removed @{name}")
                return 0
            print(f"alias not found: @{name}", file=sys.stderr)
            return 2

        print("unknown project alias subcommand", file=sys.stderr)
        return 2

    if subcmd == "unpin":
        clear_pinned_project_selection(home_dir)
        payload = {"ok": True, "pinned": {}}
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        print("cleared @pinned")
        return 0

    if subcmd == "pin":
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        entry = set_pinned_project_selection(home_dir, project_root)
        payload = {"ok": True, "pinned": entry}
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        print(f"pinned @pinned -> {entry.get('root_path')}")
        return 0

    if subcmd == "use":
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        entry = record_last_project_selection(home_dir, project_root)
        payload = {"ok": True, "last": entry}
        if bool(getattr(args, "json", False)):
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        print(f"set @last -> {entry.get('root_path')}")
        return 0

    if subcmd == "show":
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
        overlay = load_project_overlay(home_dir=home_dir, project_root=project_root)

        identity_key = str(overlay.get("identity_key") or "").strip()

        out = {
            "project_root": str(project_root),
            "project_id": pp.project_id,
            "project_dir": str(pp.project_dir),
            "overlay_path": str(pp.overlay_path),
            "identity_key": identity_key,
            "evidence_log": str(pp.evidence_log_path),
            "transcripts_dir": str(pp.transcripts_dir),
            "thoughtdb_dir": str(pp.thoughtdb_dir),
            "thoughtdb_claims": str(pp.thoughtdb_claims_path),
            "thoughtdb_edges": str(pp.thoughtdb_edges_path),
            "overlay": overlay if isinstance(overlay, dict) else {},
        }

        if bool(getattr(args, "redact", False)):
            # Redact all string leaf values for display (keeps JSON valid).
            def _redact_any(x: object) -> object:
                if isinstance(x, str):
                    return redact_text(x)
                if isinstance(x, list):
                    return [_redact_any(v) for v in x]
                if isinstance(x, dict):
                    return {k: _redact_any(v) for k, v in x.items()}
                return x

            out = _redact_any(out)  # type: ignore[assignment]

        if bool(getattr(args, "json", False)):
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0

        print(f"project_id={out['project_id']}")
        print(f"project_dir={out['project_dir']}")
        print(f"overlay_path={out['overlay_path']}")
        if identity_key:
            print(f"identity_key={identity_key}")
        print(f"evidence_log={out['evidence_log']}")
        print(f"transcripts_dir={out['transcripts_dir']}")
        print(f"thoughtdb_dir={out['thoughtdb_dir']}")
        return 0

    print("unknown project subcommand", file=sys.stderr)
    return 2
