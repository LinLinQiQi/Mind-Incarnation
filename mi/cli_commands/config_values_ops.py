from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from ..core.config import (
    apply_config_template,
    config_for_display,
    config_path,
    get_config_template,
    init_config,
    list_config_templates,
    rollback_config,
    validate_config,
)
from ..core.paths import ProjectPaths
from ..core.storage import now_rfc3339
from ..runtime.evidence import EvidenceWriter, new_run_id
from ..thoughtdb import ThoughtDbStore, claim_signature
from ..thoughtdb.operational_defaults import (
    ask_when_uncertain_claim_text,
    ensure_operational_defaults_claims_current,
    refactor_intent_claim_text,
    resolve_operational_defaults,
)
from ..thoughtdb.pins import ASK_WHEN_UNCERTAIN_TAG, REFACTOR_INTENT_TAG
from ..thoughtdb.values import VALUES_RAW_TAG, VALUES_SUMMARY_TAG, existing_values_claims


def handle_config_init_values_settings_commands(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    read_stdin_text: Callable[[], str],
    do_values_set: Callable[..., dict[str, Any]],
    make_global_tdb: Callable[[], ThoughtDbStore],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
) -> int | None:
    if args.cmd == "config":
        return _handle_config(args=args, home_dir=home_dir, cfg=cfg)
    if args.cmd == "init":
        return _handle_init(args=args, do_values_set=do_values_set, read_stdin_text=read_stdin_text)
    if args.cmd == "values":
        return _handle_values(
            args=args,
            do_values_set=do_values_set,
            read_stdin_text=read_stdin_text,
            make_global_tdb=make_global_tdb,
        )
    if args.cmd == "settings":
        return _handle_settings(
            args=args,
            home_dir=home_dir,
            cfg=cfg,
            resolve_project_root_from_args=resolve_project_root_from_args,
            effective_cd_arg=effective_cd_arg,
            make_global_tdb=make_global_tdb,
        )
    return None


def _handle_config(*, args: argparse.Namespace, home_dir: Path, cfg: dict[str, Any]) -> int:
    if args.config_cmd == "path":
        print(str(config_path(home_dir)))
        return 0
    if args.config_cmd == "init":
        path = init_config(home_dir, force=bool(args.force))
        print(f"Wrote config to {path}")
        return 0
    if args.config_cmd == "show":
        disp = config_for_display(cfg)
        print(json.dumps(disp, indent=2, sort_keys=True))
        return 0
    if args.config_cmd == "examples":
        for name in list_config_templates():
            print(name)
        return 0
    if args.config_cmd == "template":
        try:
            tmpl = get_config_template(str(args.name))
        except Exception:
            print(f"unknown template: {args.name}", file=sys.stderr)
            print("available:", file=sys.stderr)
            for name in list_config_templates():
                print(f"- {name}", file=sys.stderr)
            return 2
        print(json.dumps(tmpl, indent=2, sort_keys=True))
        return 0
    if args.config_cmd == "apply-template":
        try:
            res = apply_config_template(home_dir, name=str(args.name))
        except Exception as e:
            print(f"apply-template failed: {e}", file=sys.stderr)
            return 2
        print(f"Applied template: {args.name}")
        print(f"Backup: {res.get('backup_path')}")
        print(f"Config: {res.get('config_path')}")
        return 0
    if args.config_cmd == "rollback":
        try:
            res = rollback_config(home_dir)
        except Exception as e:
            print(f"rollback failed: {e}", file=sys.stderr)
            return 2
        print(f"Rolled back config to: {res.get('backup_path')}")
        print(f"Config: {res.get('config_path')}")
        return 0
    if args.config_cmd == "validate":
        report = validate_config(cfg)
        ok = bool(report.get("ok", False))
        errs = report.get("errors") if isinstance(report.get("errors"), list) else []
        warns = report.get("warnings") if isinstance(report.get("warnings"), list) else []
        if ok and not warns:
            print("ok")
            return 0
        if errs:
            print("errors:")
            for e in errs:
                es = str(e).strip()
                if es:
                    print(f"- {es}")
        if warns:
            print("warnings:")
            for w in warns:
                ws = str(w).strip()
                if ws:
                    print(f"- {ws}")
        return 0 if ok else 1
    return 2


def _handle_init(
    *,
    args: argparse.Namespace,
    do_values_set: Callable[..., dict[str, Any]],
    read_stdin_text: Callable[[], str],
) -> int:
    values = str(args.values or "")
    if values == "-":
        values = read_stdin_text()
    if not values.strip():
        print("Values text is empty. Provide --values or pipe text to stdin.", file=sys.stderr)
        return 2

    out = do_values_set(
        values_text=values,
        no_compile=bool(args.no_compile),
        no_values_claims=bool(args.no_values_claims),
        show=bool(args.show),
        dry_run=bool(args.dry_run),
        notes="mi init",
    )
    if not bool(out.get("ok", False)):
        print(f"init failed: {out.get('error')}", file=sys.stderr)
        return 2
    if bool(out.get("dry_run", False)):
        print("(dry-run) did not write Thought DB.")
        return 0
    ve = str(out.get("values_event_id") or "").strip()
    if ve:
        print(f"[mi] recorded global values_set event_id={ve}", file=sys.stderr)
    raw_id = str(out.get("raw_claim_id") or "").strip()
    if raw_id:
        print(f"[mi] raw values claim_id={raw_id} (tag={VALUES_RAW_TAG})", file=sys.stderr)
    sum_id = str(out.get("summary_node_id") or "").strip()
    if sum_id:
        print(f"[mi] values summary node_id={sum_id} (tag={VALUES_SUMMARY_TAG})", file=sys.stderr)

    vc = out.get("values_claims") if isinstance(out.get("values_claims"), dict) else {}
    if isinstance(vc, dict) and vc.get("skipped"):
        print(f"[mi] values->claims skipped: {vc.get('skipped')}", file=sys.stderr)
    elif isinstance(vc, dict) and isinstance(vc.get("applied"), dict):
        a = vc.get("applied") if isinstance(vc.get("applied"), dict) else {}
        written = a.get("written") if isinstance(a.get("written"), list) else []
        linked = a.get("linked_existing") if isinstance(a.get("linked_existing"), list) else []
        edges = a.get("written_edges") if isinstance(a.get("written_edges"), list) else []
        retracted = vc.get("retracted") if isinstance(vc.get("retracted"), list) else []
        print(
            f"[mi] values->claims ok: written={len(written)} linked_existing={len(linked)} edges={len(edges)} retracted={len(retracted)}",
            file=sys.stderr,
        )
    elif isinstance(vc, dict) and vc.get("error"):
        print(f"[mi] values->claims error: {vc.get('error')}", file=sys.stderr)
    return 0


def _handle_values(
    *,
    args: argparse.Namespace,
    do_values_set: Callable[..., dict[str, Any]],
    read_stdin_text: Callable[[], str],
    make_global_tdb: Callable[[], ThoughtDbStore],
) -> int:
    if args.values_cmd == "set":
        text = str(getattr(args, "text", "-") or "-")
        if text == "-":
            text = read_stdin_text()
        if not text.strip():
            print("Values text is empty. Provide --text or pipe text to stdin.", file=sys.stderr)
            return 2
        out = do_values_set(
            values_text=text,
            no_compile=bool(getattr(args, "no_compile", False)),
            no_values_claims=bool(getattr(args, "no_values_claims", False)),
            show=bool(getattr(args, "show", False)),
            dry_run=False,
            notes="mi values set",
        )
        if not bool(out.get("ok", False)):
            print(f"values set failed: {out.get('error')}", file=sys.stderr)
            return 2
        ve = str(out.get("values_event_id") or "").strip()
        if ve:
            print(f"values_event_id={ve}")
        raw_id = str(out.get("raw_claim_id") or "").strip()
        if raw_id:
            print(f"raw_claim_id={raw_id}")
        sum_id = str(out.get("summary_node_id") or "").strip()
        if sum_id:
            print(f"summary_node_id={sum_id}")
        return 0

    if args.values_cmd == "show":
        tdb = make_global_tdb()
        v = tdb.load_view(scope="global")

        raw: dict[str, Any] | None = None
        raw_ts = ""
        for c in v.iter_claims(include_inactive=False, include_aliases=False):
            if not isinstance(c, dict):
                continue
            tags = c.get("tags") if isinstance(c.get("tags"), list) else []
            tagset = {str(x).strip() for x in tags if str(x).strip()}
            if VALUES_RAW_TAG not in tagset:
                continue
            ts = str(c.get("asserted_ts") or "").strip()
            if ts >= raw_ts:
                raw = c
                raw_ts = ts

        summary: dict[str, Any] | None = None
        sum_ts = ""
        for n in v.iter_nodes(include_inactive=False, include_aliases=False):
            if not isinstance(n, dict):
                continue
            tags = n.get("tags") if isinstance(n.get("tags"), list) else []
            tagset = {str(x).strip() for x in tags if str(x).strip()}
            if VALUES_SUMMARY_TAG not in tagset:
                continue
            ts = str(n.get("asserted_ts") or "").strip()
            if ts >= sum_ts:
                summary = n
                sum_ts = ts

        derived = existing_values_claims(tdb=tdb, limit=80)
        out = {
            "raw_values_claim": raw or {},
            "values_summary_node": summary or {},
            "derived_values_claims": derived,
        }
        if bool(getattr(args, "json", False)):
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0

        if raw:
            print(f"raw_values_claim_id={raw.get('claim_id')}")
            txt = str(raw.get("text") or "").strip()
            if txt:
                print("\nvalues_text:\n" + txt)
        if summary:
            print(f"\nvalues_summary_node_id={summary.get('node_id')}")
            st = str(summary.get("text") or "").strip()
            if st:
                print("\nsummary:\n" + st)
        if derived:
            print("\nderived_values_claims:")
            for c in derived[:24]:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("claim_id") or "").strip()
                text = str(c.get("text") or "").strip()
                ct = str(c.get("claim_type") or "").strip()
                if text:
                    print(f"- [{ct or 'claim'}] {text} ({cid})" if cid else f"- [{ct or 'claim'}] {text}")
        return 0
    return 2


def _handle_settings(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
    make_global_tdb: Callable[[], ThoughtDbStore],
) -> int:
    if args.settings_cmd == "show":
        cd = effective_cd_arg(args)
        if cd:
            project_root = resolve_project_root_from_args(home_dir, cd, cfg=cfg, here=bool(getattr(args, "here", False)))
            pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
        else:
            pp = ProjectPaths(home_dir=home_dir, project_root=Path("."), _project_id="__global__")
        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)
        op = resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339())
        out = op.to_dict()
        if bool(getattr(args, "json", False)):
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0
        print(f"ask_when_uncertain={out.get('ask_when_uncertain')}")
        print(f"ask_when_uncertain_source={out.get('ask_when_uncertain_source')}")
        print(f"refactor_intent={out.get('refactor_intent')}")
        print(f"refactor_intent_source={out.get('refactor_intent_source')}")
        return 0

    if args.settings_cmd == "set":
        scope = str(getattr(args, "scope", "global") or "global").strip()
        ask_s = str(getattr(args, "ask_when_uncertain", "") or "").strip()
        ref_s = str(getattr(args, "refactor_intent", "") or "").strip()
        if not ask_s and not ref_s:
            print("nothing to set: provide --ask-when-uncertain and/or --refactor-intent", file=sys.stderr)
            return 2

        if scope == "global":
            tdb = make_global_tdb()
            cur = resolve_operational_defaults(tdb=tdb, as_of_ts=now_rfc3339())
            desired_ask = cur.ask_when_uncertain
            desired_ref = cur.refactor_intent or "behavior_preserving"
            if ask_s:
                desired_ask = ask_s == "ask"
            if ref_s:
                desired_ref = ref_s

            desired = {"ask_when_uncertain": desired_ask, "refactor_intent": desired_ref}
            if bool(getattr(args, "dry_run", False)):
                print(json.dumps({"scope": "global", "desired": desired}, indent=2, sort_keys=True))
                return 0

            res = ensure_operational_defaults_claims_current(
                home_dir=home_dir,
                tdb=tdb,
                desired_defaults=desired,
                mode="sync",
                event_notes="mi settings set",
                claim_notes_prefix="user_set",
            )
            print(json.dumps(res, indent=2, sort_keys=True))
            return 0

        # Project-scoped overrides: write setting claims into the project store (append-only).
        project_root = resolve_project_root_from_args(
            home_dir,
            effective_cd_arg(args),
            cfg=cfg,
            here=bool(getattr(args, "here", False)),
        )
        pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)
        evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
        ev = evw.append(
            {
                "kind": "settings_set",
                "batch_id": "cli.settings_set",
                "ts": now_rfc3339(),
                "thread_id": "",
                "scope": "project",
                "project_id": pp.project_id,
                "ask_when_uncertain": ask_s,
                "refactor_intent": ref_s,
            }
        )
        ev_id = str(ev.get("event_id") or "").strip()

        v = tdb.load_view(scope="project")
        sig_map = tdb.existing_signature_map(scope="project")

        def _find_latest_tagged(tag: str) -> dict[str, Any] | None:
            best: dict[str, Any] | None = None
            best_ts = ""
            for c in v.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=now_rfc3339()):
                if not isinstance(c, dict):
                    continue
                ct = str(c.get("claim_type") or "").strip()
                if ct not in ("preference", "goal"):
                    continue
                tags = c.get("tags") if isinstance(c.get("tags"), list) else []
                tagset = {str(x).strip() for x in tags if str(x).strip()}
                if tag not in tagset:
                    continue
                ts = str(c.get("asserted_ts") or "").strip()
                if ts >= best_ts:
                    best = c
                    best_ts = ts
            return best

        changes: list[dict[str, str]] = []
        for tag, text in (
            ("ask", ask_when_uncertain_claim_text(True) if ask_s == "ask" else ask_when_uncertain_claim_text(False) if ask_s else ""),
            ("ref", refactor_intent_claim_text(ref_s) if ref_s else ""),
        ):
            if not text:
                continue
            tag_id = ASK_WHEN_UNCERTAIN_TAG if tag == "ask" else REFACTOR_INTENT_TAG
            existing = _find_latest_tagged(tag_id)
            existing_id = str(existing.get("claim_id") or "").strip() if isinstance(existing, dict) else ""
            existing_text = str(existing.get("text") or "").strip() if isinstance(existing, dict) else ""
            if existing_id and existing_text == text:
                continue

            sig = claim_signature(claim_type="preference", scope="project", project_id=pp.project_id, text=text)
            reuse_id = ""
            if sig and sig in sig_map:
                cand = v.claims_by_id.get(str(sig_map[sig]) or "")
                cand_tags = cand.get("tags") if isinstance(cand, dict) and isinstance(cand.get("tags"), list) else []
                cand_tagset = {str(x).strip() for x in cand_tags if str(x).strip()}
                if tag_id in cand_tagset:
                    reuse_id = str(sig_map[sig])

            if reuse_id:
                new_id = reuse_id
            else:
                new_id = tdb.append_claim_create(
                    claim_type="preference",
                    text=text,
                    scope="project",
                    visibility="project",
                    valid_from=None,
                    valid_to=None,
                    tags=[tag_id, "mi:setting", "mi:defaults", "mi:source:cli.settings_set"],
                    source_event_ids=[ev_id] if ev_id else [],
                    confidence=1.0,
                    notes="user_set project override",
                )

            if existing_id and ev_id:
                try:
                    tdb.append_edge(
                        edge_type="supersedes",
                        from_id=existing_id,
                        to_id=new_id,
                        scope="project",
                        visibility="project",
                        source_event_ids=[ev_id],
                        notes="project settings override update",
                    )
                except Exception:
                    pass
            changes.append({"tag": tag_id, "claim_id": new_id})

        print(json.dumps({"ok": True, "scope": "project", "project_id": pp.project_id, "changes": changes}, indent=2, sort_keys=True))
        return 0
    return 2
