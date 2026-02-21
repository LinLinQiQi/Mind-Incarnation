from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from ..core.paths import ProjectPaths
from ..core.storage import iter_jsonl, now_rfc3339
from ..providers.provider_factory import make_mind_provider
from ..runtime.evidence import EvidenceWriter, new_run_id
from ..runtime.inspect import tail_json_objects
from ..runtime.prompts import mine_claims_prompt
from ..thoughtdb import ThoughtDbStore, claim_signature
from ..thoughtdb.app_service import ThoughtDbApplicationService
from ..project.overlay_store import load_project_overlay

def handle_claim_commands(
    *,
    args: argparse.Namespace,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[argparse.Namespace], str],
) -> int | None:
    if args.cmd == "claim":
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
        overlay2 = load_project_overlay(home_dir=home_dir, project_root=project_root)
        if not isinstance(overlay2, dict):
            overlay2 = {}

        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)
        tdb_app = ThoughtDbApplicationService(tdb=tdb, project_paths=pp)

        def _iter_effective_claims(
            *,
            include_inactive: bool,
            include_aliases: bool,
            as_of_ts: str,
            filter_fn: Any,
        ) -> list[dict]:
            return tdb_app.list_effective_claims(
                include_inactive=include_inactive,
                include_aliases=include_aliases,
                as_of_ts=as_of_ts,
                filter_fn=filter_fn,
            )

        def _find_claim_effective(cid: str) -> tuple[str, dict[str, Any] | None]:
            """Return (scope, claim) searching project then global."""
            return tdb_app.find_claim_effective(cid)

        if args.claim_cmd == "list":
            scope = str(getattr(args, "scope", "project") or "project").strip()
            raw_statuses = getattr(args, "status", None) or []
            want_statuses = {str(x).strip() for x in raw_statuses if str(x).strip()}
            include_inactive = bool(getattr(args, "all", False)) or (bool(want_statuses) and want_statuses != {"active"})
            include_aliases = bool(getattr(args, "all", False))

            raw_tags = getattr(args, "tag", None) or []
            want_tags = {str(x).strip().lower() for x in raw_tags if str(x).strip()}
            contains = str(getattr(args, "contains", "") or "").strip().lower()
            raw_types = getattr(args, "claim_type", None) or []
            want_types = {str(x).strip() for x in raw_types if str(x).strip()}
            try:
                limit = int(getattr(args, "limit", 0) or 0)
            except Exception:
                limit = 0
            as_of_ts = str(getattr(args, "as_of", "") or "").strip() or now_rfc3339()

            def _claim_matches(c: dict[str, Any]) -> bool:
                if want_types and str(c.get("claim_type") or "").strip() not in want_types:
                    return False
                if want_statuses and str(c.get("status") or "").strip() not in want_statuses:
                    return False
                if want_tags:
                    tags = c.get("tags") if isinstance(c.get("tags"), list) else []
                    tagset = {str(x).strip().lower() for x in tags if str(x).strip()}
                    if not all(t in tagset for t in want_tags):
                        return False
                if contains:
                    text = str(c.get("text") or "")
                    if contains not in text.lower():
                        return False
                return True

            if scope == "effective":
                items = _iter_effective_claims(
                    include_inactive=include_inactive,
                    include_aliases=include_aliases,
                    as_of_ts=as_of_ts,
                    filter_fn=_claim_matches,
                )
            else:
                v = tdb.load_view(scope=scope)
                items = [
                    x
                    for x in v.iter_claims(include_inactive=include_inactive, include_aliases=include_aliases, as_of_ts=as_of_ts)
                    if isinstance(x, dict) and _claim_matches(x)
                ]
                items.sort(key=lambda x: str(x.get("asserted_ts") or ""), reverse=True)

            if limit > 0:
                items = items[:limit]

            if getattr(args, "json", False):
                print(json.dumps(items, indent=2, sort_keys=True))
                return 0

            if not items:
                print("(no claims)")
                return 0
            for c in items:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("claim_id") or "").strip()
                ct = str(c.get("claim_type") or "").strip()
                st = str(c.get("status") or "").strip()
                sc = str(c.get("scope") or scope).strip()
                text = str(c.get("text") or "").strip().replace("\n", " ")
                if len(text) > 140:
                    text = text[:137] + "..."
                print(f"{cid} scope={sc} status={st} type={ct} {text}".strip())
            return 0

        if args.claim_cmd == "show":
            cid = str(args.id or "").strip()
            scope = str(getattr(args, "scope", "effective") or "effective").strip()
            found_scope = ""
            obj: dict[str, Any] | None = None
            edges: list[dict[str, Any]] = []

            want_graph = bool(getattr(args, "graph", False))
            if want_graph and not bool(getattr(args, "json", False)):
                print("--graph requires --json", file=sys.stderr)
                return 2

            if scope == "effective":
                found_scope, obj = _find_claim_effective(cid)
                if found_scope:
                    edges = tdb_app.related_edges_for_id(scope=found_scope, item_id=cid)
            else:
                found_scope, obj = tdb_app.find_claim(scope=scope, claim_id=cid)
                if found_scope:
                    edges = tdb_app.related_edges_for_id(scope=found_scope, item_id=cid)

            if not obj:
                print(f"claim not found: {cid}", file=sys.stderr)
                return 2

            payload = {"scope": found_scope, "claim": obj, "edges": edges}
            if want_graph:
                edge_types_raw = getattr(args, "edge_types", None) or []
                etypes = {str(x).strip() for x in edge_types_raw if str(x).strip()}
                graph_scope = scope if scope == "effective" else found_scope
                payload["graph"] = tdb_app.build_subgraph(
                    scope=graph_scope,
                    root_id=str(obj.get("claim_id") or cid).strip() or cid,
                    depth=int(getattr(args, "depth", 1) or 1),
                    direction=str(getattr(args, "direction", "both") or "both").strip(),
                    edge_types=etypes,
                    include_inactive=bool(getattr(args, "include_inactive", False)),
                    include_aliases=bool(getattr(args, "include_aliases", False)),
                )
            if getattr(args, "json", False):
                print(json.dumps(payload, indent=2, sort_keys=True))
                return 0

            c = obj
            print(f"claim_id={c.get('claim_id')}")
            if c.get("requested_id") and c.get("requested_id") != c.get("claim_id"):
                print(f"requested_id={c.get('requested_id')}")
            print(f"scope={found_scope}")
            print(f"type={c.get('claim_type')}")
            print(f"status={c.get('status')}")
            canon = c.get("canonical_id")
            if canon and canon != c.get("claim_id"):
                print(f"canonical_id={canon}")
            text = str(c.get("text") or "").strip()
            if text:
                print("text:")
                print(text)
            if edges:
                print(f"edges={len(edges)}")
            return 0

        if args.claim_cmd == "apply-suggested":
            sug_id = str(getattr(args, "suggestion_id", "") or "").strip()
            if not sug_id:
                print("missing suggestion_id", file=sys.stderr)
                return 2

            suggestion: dict[str, object] | None = None
            for obj in iter_jsonl(pp.evidence_log_path):
                if not isinstance(obj, dict):
                    continue
                if obj.get("kind") != "learn_suggested":
                    continue
                if str(obj.get("id") or "") == sug_id:
                    suggestion = obj

            if suggestion is None:
                print(f"suggestion not found: {sug_id}", file=sys.stderr)
                return 2

            # Avoid duplicate application unless forced.
            already_applied = False
            applied_ids0 = suggestion.get("applied_claim_ids")
            if isinstance(applied_ids0, list) and any(str(x).strip() for x in applied_ids0):
                already_applied = True

            if not already_applied:
                for obj in iter_jsonl(pp.evidence_log_path):
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") != "learn_applied":
                        continue
                    if str(obj.get("suggestion_id") or "") == sug_id:
                        already_applied = True
                        break

            if already_applied and not bool(getattr(args, "force", False)):
                print(f"Suggestion already applied: {sug_id}")
                return 0

            changes = suggestion.get("learn_suggested") if isinstance(suggestion.get("learn_suggested"), list) else []
            normalized: list[dict[str, str]] = []
            for ch in changes:
                if not isinstance(ch, dict):
                    continue
                scope = str(ch.get("scope") or "").strip()
                text = str(ch.get("text") or "").strip()
                if scope not in ("global", "project") or not text:
                    continue
                normalized.append(
                    {
                        "scope": scope,
                        "text": text,
                        "rationale": str(ch.get("rationale") or "").strip(),
                        "severity": str(ch.get("severity") or "").strip(),
                    }
                )

            if not normalized:
                print(f"(no applicable learn_suggested items in suggestion {sug_id})")
                return 0

            if bool(getattr(args, "dry_run", False)):
                print(json.dumps({"suggestion_id": sug_id, "changes": normalized}, indent=2, sort_keys=True))
                return 0

            extra = str(getattr(args, "extra_rationale", "") or "").strip()
            sig_to_id = {
                "project": tdb.existing_signature_map(scope="project"),
                "global": tdb.existing_signature_map(scope="global"),
            }
            ev_id = str(suggestion.get("event_id") or "").strip()
            src_eids = [ev_id] if ev_id else []

            applied_claim_ids: list[str] = []
            for item in normalized:
                scope0 = str(item.get("scope") or "").strip()
                sc = "global" if scope0 == "global" else "project"
                pid = pp.project_id if sc == "project" else ""
                text = str(item.get("text") or "").strip()
                if not text:
                    continue

                sig = claim_signature(claim_type="preference", scope=sc, project_id=pid, text=text)
                existing = sig_to_id.get(sc, {}).get(sig)
                if existing:
                    applied_claim_ids.append(str(existing))
                    continue

                base_r = (item.get("rationale") or "").strip() or "manual_apply"
                notes = f"{base_r} (apply_suggestion={sug_id})"
                if extra:
                    notes = f"{notes}; {extra}"
                sev = str(item.get("severity") or "").strip()
                tags = ["mi:learned_apply", f"learn_suggested:{sug_id}"]
                if sev:
                    tags.append(f"severity:{sev}")

                cid = tdb.append_claim_create(
                    claim_type="preference",
                    text=text,
                    scope=sc,
                    visibility=("global" if sc == "global" else "project"),
                    valid_from=None,
                    valid_to=None,
                    tags=tags,
                    source_event_ids=src_eids,
                    confidence=1.0,
                    notes=notes,
                )
                sig_to_id.setdefault(sc, {})[sig] = cid
                applied_claim_ids.append(cid)

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            evw.append(
                {
                    "kind": "learn_applied",
                    "ts": now_rfc3339(),
                    "suggestion_id": sug_id,
                    "batch_id": str(suggestion.get("batch_id") or ""),
                    "thread_id": str(suggestion.get("thread_id") or ""),
                    "applied_claim_ids": applied_claim_ids,
                }
            )
            print(f"Applied suggestion {sug_id}: {len(applied_claim_ids)} preference claims")
            for cid in applied_claim_ids:
                print(cid)
            return 0

        if args.claim_cmd == "retract":
            cid = str(args.id or "").strip()
            scope = str(getattr(args, "scope", "project") or "project").strip()

            # Record a user-driven event in EvidenceLog and cite it in Thought DB.
            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            ev = evw.append(
                {
                    "kind": "claim_retract",
                    "batch_id": "cli.claim_retract",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "scope": scope,
                    "claim_id": cid,
                    "rationale": str(getattr(args, "rationale", "") or "").strip(),
                }
            )
            try:
                tdb.append_claim_retract(
                    claim_id=cid,
                    scope=scope,
                    rationale=str(getattr(args, "rationale", "") or "").strip(),
                    source_event_ids=[str(ev.get("event_id") or "").strip()],
                )
            except Exception as e:
                print(f"retract failed: {e}", file=sys.stderr)
                return 2
            print(cid)
            return 0

        if args.claim_cmd == "supersede":
            old_id = str(args.id or "").strip()
            scope = str(getattr(args, "scope", "effective") or "effective").strip()

            if scope == "effective":
                found_scope, old = _find_claim_effective(old_id)
                if not old or not found_scope:
                    print(f"old claim not found: {old_id}", file=sys.stderr)
                    return 2
                scope = found_scope
            else:
                v = tdb.load_view(scope=scope)
                old = dict(v.claims_by_id.get(old_id) or {})
                if not old:
                    print(f"old claim not found: {old_id}", file=sys.stderr)
                    return 2

            new_text = str(getattr(args, "text", "") or "").strip()
            if not new_text:
                print("--text is required", file=sys.stderr)
                return 2

            ct = str(getattr(args, "claim_type", "") or "").strip() or str(old.get("claim_type") or "").strip() or "fact"
            vis = str(getattr(args, "visibility", "") or "").strip() or str(old.get("visibility") or "").strip() or ("global" if scope == "global" else "project")
            vf = str(getattr(args, "valid_from", "") or "").strip() or None
            vt = str(getattr(args, "valid_to", "") or "").strip() or None
            tags = [str(x).strip() for x in (getattr(args, "tag", None) or []) if str(x).strip()]

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            ev = evw.append(
                {
                    "kind": "claim_supersede",
                    "batch_id": "cli.claim_supersede",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "scope": scope,
                    "old_claim_id": old_id,
                    "new_text": new_text,
                    "claim_type": ct,
                    "visibility": vis,
                    "valid_from": vf,
                    "valid_to": vt,
                    "tags": tags,
                }
            )
            ev_id = str(ev.get("event_id") or "").strip()
            try:
                new_id = tdb.append_claim_create(
                    claim_type=ct,
                    text=new_text,
                    scope=scope,
                    visibility=vis,
                    valid_from=vf,
                    valid_to=vt,
                    tags=tags,
                    source_event_ids=[ev_id] if ev_id else [],
                    confidence=1.0,
                    notes="supersede via cli",
                )
                tdb.append_edge(
                    edge_type="supersedes",
                    from_id=old_id,
                    to_id=new_id,
                    scope=scope,
                    visibility=vis,
                    source_event_ids=[ev_id] if ev_id else [],
                    notes="supersede via cli",
                )
            except Exception as e:
                print(f"supersede failed: {e}", file=sys.stderr)
                return 2
            print(new_id)
            return 0

        if args.claim_cmd == "same-as":
            dup_id = str(args.dup_id or "").strip()
            canon_id = str(args.canonical_id or "").strip()
            scope = str(getattr(args, "scope", "project") or "project").strip()

            v = tdb.load_view(scope=scope)
            if dup_id not in v.claims_by_id or canon_id not in v.claims_by_id:
                print("both dup_id and canonical_id must exist in the same scope store", file=sys.stderr)
                return 2

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            ev = evw.append(
                {
                    "kind": "claim_same_as",
                    "batch_id": "cli.claim_same_as",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "scope": scope,
                    "dup_id": dup_id,
                    "canonical_id": canon_id,
                    "notes": str(getattr(args, "notes", "") or "").strip(),
                }
            )
            ev_id = str(ev.get("event_id") or "").strip()
            try:
                tdb.append_edge(
                    edge_type="same_as",
                    from_id=dup_id,
                    to_id=canon_id,
                    scope=scope,
                    visibility=str(v.claims_by_id.get(dup_id, {}).get("visibility") or ("global" if scope == "global" else "project")),
                    source_event_ids=[ev_id] if ev_id else [],
                    notes=str(getattr(args, "notes", "") or "").strip(),
                )
            except Exception as e:
                print(f"same-as failed: {e}", file=sys.stderr)
                return 2
            print(f"{dup_id} -> {canon_id}")
            return 0

        if args.claim_cmd == "mine":
            # On-demand mining uses the Mind provider (same as other CLI model calls).
            runtime_cfg = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
            tcfg = runtime_cfg.get("thought_db") if isinstance(runtime_cfg.get("thought_db"), dict) else {}
            try:
                min_conf = float(tcfg.get("min_confidence", 0.9) or 0.9)
            except Exception:
                min_conf = 0.9
            try:
                max_claims = int(tcfg.get("max_claims_per_checkpoint", 6) or 6)
            except Exception:
                max_claims = 6
            if getattr(args, "min_confidence", -1.0) is not None and float(getattr(args, "min_confidence")) >= 0:
                min_conf = float(getattr(args, "min_confidence"))
            if getattr(args, "max_claims", -1) is not None and int(getattr(args, "max_claims")) >= 0:
                max_claims = int(getattr(args, "max_claims"))

            # Prefer the current open segment buffer; fall back to EvidenceLog tail.
            seg: dict[str, Any] | None = None
            try:
                seg = json.loads(pp.segment_state_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                seg = None
            except Exception:
                seg = None

            seg_records: list[dict[str, Any]] = []
            if isinstance(seg, dict) and bool(seg.get("open", False)) and isinstance(seg.get("records"), list):
                seg_records = [x for x in seg.get("records") if isinstance(x, dict)]  # type: ignore[arg-type]
            if not seg_records:
                seg_records = tail_json_objects(pp.evidence_log_path, 60)

            allowed: list[str] = []
            seen: set[str] = set()
            for r in seg_records:
                eid = r.get("event_id")
                if isinstance(eid, str) and eid.strip() and eid.strip() not in seen:
                    seen.add(eid.strip())
                    allowed.append(eid.strip())
            allowed_set = set(allowed)

            pp.transcripts_dir.mkdir(parents=True, exist_ok=True)
            mind = make_mind_provider(cfg, project_root=project_root, transcripts_dir=pp.transcripts_dir)
            tdb_ctx = tdb_app.build_decide_context(
                as_of_ts=now_rfc3339(),
                task=str("(manual claim mine) " + (seg.get("task_hint") if isinstance(seg, dict) else "")).strip(),
                hands_last_message="",
                recent_evidence=seg_records[-8:],
            )
            tdb_ctx_obj = tdb_ctx.to_prompt_obj()
            prompt = mine_claims_prompt(
                task=str("(manual claim mine) " + (seg.get("task_hint") if isinstance(seg, dict) else "")).strip(),
                hands_provider=str(cfg.get("hands", {}).get("provider") or ""),
                mindspec_base=runtime_cfg,
                project_overlay=overlay2,
                thought_db_context=tdb_ctx_obj,
                segment_evidence=seg_records,
                allowed_event_ids=allowed,
                min_confidence=min_conf,
                max_claims=max_claims,
                notes="source=cli",
            )
            try:
                res = mind.call(schema_filename="mine_claims.json", prompt=prompt, tag="mine_claims_cli")
            except Exception as e:
                print(f"mind call failed: {e}", file=sys.stderr)
                return 2

            out = res.obj if hasattr(res, "obj") else {}
            applied = tdb.apply_mined_output(
                output=out if isinstance(out, dict) else {},
                allowed_event_ids=allowed_set,
                min_confidence=min_conf,
                max_claims=max_claims,
            )

            evw = EvidenceWriter(path=pp.evidence_log_path, run_id=new_run_id("cli"))
            evw.append(
                {
                    "kind": "claim_mining",
                    "batch_id": "cli.claim_mining",
                    "ts": now_rfc3339(),
                    "thread_id": "",
                    "segment_id": str(seg.get("segment_id") or "") if isinstance(seg, dict) else "",
                    "mind_transcript_ref": str(getattr(res, "transcript_path", "") or ""),
                    "config": {"min_confidence": min_conf, "max_claims_per_checkpoint": max_claims},
                    "output": out if isinstance(out, dict) else {},
                    "applied": applied,
                }
            )

            if getattr(args, "json", False):
                print(json.dumps({"applied": applied, "output": out}, indent=2, sort_keys=True))
                return 0
            written = applied.get("written") if isinstance(applied, dict) else []
            print(f"written={len(written) if isinstance(written, list) else 0}")
            return 0

        print("unknown claim subcommand", file=sys.stderr)
        return 2


    return None
