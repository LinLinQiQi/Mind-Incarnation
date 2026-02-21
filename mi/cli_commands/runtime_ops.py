from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from ..core.paths import GlobalPaths, ProjectPaths
from ..memory.service import MemoryService
from ..providers.provider_factory import make_hands_functions, make_mind_provider
from ..runtime.gc import archive_project_transcripts
from ..runtime.runner import run_autopilot
from ..thoughtdb import ThoughtDbStore
from ..thoughtdb.compact import compact_thoughtdb_dir


def handle_run_memory_gc_commands(
    *,
    args: Any,
    home_dir: Path,
    cfg: dict[str, Any],
    resolve_project_root_from_args: Callable[..., Path],
    effective_cd_arg: Callable[[Any], str],
) -> int | None:
    if args.cmd == "run":
        quiet = bool(getattr(args, "quiet", False))
        live = not quiet
        hands_raw = bool(getattr(args, "hands_raw", False))
        no_mi_prompt = bool(getattr(args, "no_mi_prompt", False))
        run_redact = bool(getattr(args, "redact", False))

        task_obj = getattr(args, "task", "")
        if isinstance(task_obj, list):
            task = " ".join(str(x) for x in task_obj).strip()
        else:
            task = str(task_obj or "").strip()

        hands_exec, hands_resume = make_hands_functions(cfg, live=live, hands_raw=hands_raw, redact=run_redact)
        project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
        project_paths = ProjectPaths(home_dir=home_dir, project_root=project_root)
        llm = make_mind_provider(cfg, project_root=project_root, transcripts_dir=project_paths.transcripts_dir)
        hands_provider = ""
        hands_cfg = cfg.get("hands") if isinstance(cfg.get("hands"), dict) else {}
        if isinstance(hands_cfg, dict):
            hands_provider = str(hands_cfg.get("provider") or "").strip()
        continue_default = bool(hands_cfg.get("continue_across_runs", False)) if isinstance(hands_cfg, dict) else False
        continue_hands = bool(args.continue_hands or continue_default)
        result = run_autopilot(
            task=task,
            project_root=str(project_root),
            home_dir=str(home_dir),
            max_batches=args.max_batches,
            hands_exec=hands_exec,
            hands_resume=hands_resume,
            llm=llm,
            hands_provider=hands_provider,
            continue_hands=continue_hands,
            reset_hands=bool(args.reset_hands),
            why_trace_on_run_end=bool(getattr(args, "why", False)),
            live=live,
            quiet=quiet,
            no_mi_prompt=no_mi_prompt,
            redact=run_redact,
        )
        # Always print an end summary unless suppressed.
        if not quiet:
            print(result.render_text())
        return 0 if result.status == "done" else 1

    if args.cmd == "memory":
        if args.mem_cmd == "index":
            mem = MemoryService(home_dir)
            if args.mi_cmd == "status":
                st = mem.status()
                if args.json:
                    print(json.dumps(st, indent=2, sort_keys=True))
                    return 0
                backend = str(st.get("backend") or "?").strip() or "?"
                exists = bool(st.get("exists", True))
                if not exists:
                    db_path = str(st.get("db_path") or "").strip()
                    extra = f" {db_path}" if db_path else ""
                    print(f"memory backend: {backend} (missing){extra}")
                    return 0

                print(f"memory backend: {backend}")
                if str(st.get("db_path") or "").strip():
                    print(f"db_path: {st.get('db_path')}")
                if str(st.get("fts_version") or "").strip():
                    print(f"fts_version: {st.get('fts_version')}")
                print(f"total_items: {st.get('total_items')}")
                groups = st.get("groups") if isinstance(st.get("groups"), list) else []
                if groups:
                    print("groups:")
                    for g in groups:
                        if not isinstance(g, dict):
                            continue
                        proj = str(g.get("project_id") or "").strip() or "global"
                        kind = str(g.get("kind") or "").strip() or "?"
                        scope = str(g.get("scope") or "").strip() or "?"
                        try:
                            n = int(g.get("count") or 0)
                        except Exception:
                            n = 0
                        print(f"- {kind}/{scope}/{proj}: {n}")
                return 0

            if args.mi_cmd == "rebuild":
                res = mem.rebuild(include_snapshots=not bool(args.no_snapshots))
                if args.json:
                    print(json.dumps(res, indent=2, sort_keys=True))
                    return 0
                print(f"rebuilt: {bool(res.get('rebuilt', False))}")
                backend = str(res.get("backend") or "?").strip() or "?"
                print(f"backend: {backend}")
                if str(res.get("db_path") or "").strip():
                    print(f"db_path: {res.get('db_path')}")
                if str(res.get("fts_version") or "").strip():
                    print(f"fts_version: {res.get('fts_version')}")
                print(f"total_items: {res.get('total_items')}")
                if "indexed_snapshots" in res:
                    print(f"indexed_snapshots: {res.get('indexed_snapshots')}")
                return 0

    if args.cmd == "gc":
        if args.gc_cmd == "transcripts":
            project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
            pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
            res = archive_project_transcripts(
                transcripts_dir=pp.transcripts_dir,
                keep_hands=int(args.keep_hands),
                keep_mind=int(args.keep_mind),
                dry_run=not bool(args.apply),
            )
            if args.json:
                print(json.dumps(res, indent=2, sort_keys=True))
                return 0

            mode = "dry-run" if res.get("dry_run") else "applied"
            hands = res.get("hands") if isinstance(res.get("hands"), dict) else {}
            mind = res.get("mind") if isinstance(res.get("mind"), dict) else {}
            print(f"{mode} project_dir={pp.project_dir}")
            print(f"hands: keep={hands.get('keep')} planned={hands.get('planned')}")
            print(f"mind: keep={mind.get('keep')} planned={mind.get('planned')}")
            if not bool(args.apply):
                print("Re-run with --apply to archive.")
            return 0

        if args.gc_cmd == "thoughtdb":
            dry_run = not bool(getattr(args, "apply", False))
            is_global = bool(getattr(args, "gc_global", False))

            if is_global:
                gp = GlobalPaths(home_dir=home_dir)
                snap = gp.thoughtdb_global_dir / "view.snapshot.json"
                res = compact_thoughtdb_dir(thoughtdb_dir=gp.thoughtdb_global_dir, snapshot_path=snap, dry_run=dry_run)
                res["scope"] = "global"
            else:
                project_root = resolve_project_root_from_args(home_dir, effective_cd_arg(args), cfg=cfg, here=bool(getattr(args, "here", False)))
                pp = ProjectPaths(home_dir=home_dir, project_root=project_root)
                snap = pp.thoughtdb_dir / "view.snapshot.json"
                res = compact_thoughtdb_dir(thoughtdb_dir=pp.thoughtdb_dir, snapshot_path=snap, dry_run=dry_run)
                res["scope"] = "project"
                res["project_id"] = pp.project_id
                res["project_dir"] = str(pp.project_dir)

            # Rebuild snapshot after applying compaction (best-effort).
            res["snapshot"] = res.get("snapshot") if isinstance(res.get("snapshot"), dict) else {"path": str(snap)}
            if not dry_run:
                try:
                    if is_global:
                        dummy_pp = ProjectPaths(home_dir=home_dir, project_root=Path("."), _project_id="__global__")
                        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=dummy_pp)
                        tdb.load_view(scope="global")
                    else:
                        tdb = ThoughtDbStore(home_dir=home_dir, project_paths=pp)  # type: ignore[arg-type]
                        tdb.load_view(scope="project")
                    res["snapshot"]["rebuilt"] = True
                except Exception as e:
                    res["snapshot"]["rebuilt"] = False
                    res["snapshot"]["rebuild_error"] = f"{type(e).__name__}: {e}"

            if args.json:
                print(json.dumps(res, indent=2, sort_keys=True))
                return 0

            mode = "dry-run" if res.get("dry_run") else "applied"
            scope = str(res.get("scope") or "").strip() or ("global" if is_global else "project")
            print(f"{mode} scope={scope} thoughtdb_dir={res.get('thoughtdb_dir')}")
            files = res.get("files") if isinstance(res.get("files"), dict) else {}
            for name in ("claims", "edges", "nodes"):
                item = files.get(name) if isinstance(files.get(name), dict) else {}
                w = item.get("write") if isinstance(item.get("write"), dict) else {}
                cs = item.get("compact_stats") if isinstance(item.get("compact_stats"), dict) else {}
                planned = w.get("lines") if isinstance(w.get("lines"), int) else cs.get("output_lines")
                inp = cs.get("input_lines")
                print(f"{name}: input_lines={inp} output_lines={planned}")
            if dry_run:
                print("Re-run with --apply to compact and archive.")
            return 0

    return None
