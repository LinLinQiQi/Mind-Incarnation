import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__
from .mindspec import MindSpecStore
from .llm import MiLlm
from .prompts import compile_mindspec_prompt
from .runner import run_autopilot
from .paths import ProjectPaths, default_home_dir
from .inspect import load_last_batch_bundle, tail_raw_lines, tail_json_objects, summarize_evidence_record
from .transcript import last_agent_message_from_transcript
from .redact import redact_text


def _read_stdin_text() -> str:
    data = sys.stdin.read()
    return data.strip("\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mi", description="Mind Incarnation (MI) V1 wrapper for Codex.")
    parser.add_argument(
        "--home",
        default=os.environ.get("MI_HOME"),
        help="MI home directory (defaults to $MI_HOME or ~/.mind-incarnation).",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version", help="Print MI version.")

    p_init = sub.add_parser("init", help="Initialize global values/preferences (MindSpec base).")
    p_init.add_argument(
        "--values",
        help="Values/preferences prompt text. If omitted or '-', read from stdin.",
        default="-",
    )
    p_init.add_argument(
        "--no-compile",
        action="store_true",
        help="Do not call the model; write defaults + values_text only.",
    )
    p_init.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the compiled MindSpec but do not write it.",
    )
    p_init.add_argument(
        "--show",
        action="store_true",
        help="Print the compiled values summary and decision procedure.",
    )

    p_run = sub.add_parser("run", help="Run MI batch autopilot with Codex as Hands.")
    p_run.add_argument("task", help="User task for Codex to execute.")
    p_run.add_argument(
        "--cd",
        default=os.getcwd(),
        help="Working directory for the Codex run (project root).",
    )
    p_run.add_argument(
        "--max-batches",
        type=int,
        default=8,
        help="Maximum number of Codex batches before stopping.",
    )
    p_run.add_argument(
        "--show",
        action="store_true",
        help="Print MI summaries plus pointers to raw transcript and evidence log.",
    )

    p_learned = sub.add_parser("learned", help="Inspect or rollback learned preferences.")
    learned_sub = p_learned.add_subparsers(dest="learned_cmd", required=True)

    p_ll = learned_sub.add_parser("list", help="List learned entries (global + project).")
    p_ll.add_argument("--cd", default=os.getcwd(), help="Project root used for project-scoped learned entries.")

    p_ld = learned_sub.add_parser("disable", help="Disable a learned entry by id (append-only).")
    p_ld.add_argument("id", help="Learned change id to disable.")
    p_ld.add_argument(
        "--scope",
        choices=["global", "project"],
        default="project",
        help="Where to record the disable action. 'project' disables only for this project; 'global' disables everywhere.",
    )
    p_ld.add_argument("--cd", default=os.getcwd(), help="Project root used for project-scoped disable.")
    p_ld.add_argument("--rationale", default="user rollback", help="Reason to record for the rollback.")

    p_last = sub.add_parser("last", help="Show the latest MI batch bundle (input/output/evidence pointers).")
    p_last.add_argument("--cd", default=os.getcwd(), help="Project root used to locate MI artifacts.")
    p_last.add_argument("--json", action="store_true", help="Print as JSON.")
    p_last.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

    p_evidence = sub.add_parser("evidence", help="Inspect EvidenceLog (JSONL).")
    ev_sub = p_evidence.add_subparsers(dest="evidence_cmd", required=True)
    p_ev_tail = ev_sub.add_parser("tail", help="Tail EvidenceLog records.")
    p_ev_tail.add_argument("--cd", default=os.getcwd(), help="Project root used to locate MI artifacts.")
    p_ev_tail.add_argument("-n", "--lines", type=int, default=20, help="Number of records to show.")
    p_ev_tail.add_argument("--raw", action="store_true", help="Print raw JSONL lines.")
    p_ev_tail.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

    p_tr = sub.add_parser("transcript", help="Inspect raw transcripts (Hands or Mind).")
    tr_sub = p_tr.add_subparsers(dest="tr_cmd", required=True)
    p_tr_show = tr_sub.add_parser("show", help="Show a transcript (defaults to the latest Hands transcript).")
    p_tr_show.add_argument("--cd", default=os.getcwd(), help="Project root used to locate MI artifacts.")
    p_tr_show.add_argument("--mind", action="store_true", help="Show Mind transcript instead of Hands.")
    p_tr_show.add_argument("--path", default="", help="Explicit transcript path to show (overrides --mind/--cd selection).")
    p_tr_show.add_argument("-n", "--lines", type=int, default=200, help="Number of transcript lines to show (tail).")
    p_tr_show.add_argument("--jsonl", action="store_true", help="Print stored JSONL lines (no pretty formatting).")
    p_tr_show.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

    args = parser.parse_args(argv)

    store = MindSpecStore(home_dir=args.home)

    if args.cmd == "version":
        print(__version__)
        return 0

    if args.cmd == "init":
        values = args.values
        if values == "-" or values is None:
            values = _read_stdin_text()
        if not values.strip():
            print("Values text is empty. Provide --values or pipe text to stdin.", file=sys.stderr)
            return 2

        compiled = None
        if not args.no_compile:
            # Run compile in an isolated directory to avoid accidental project context bleed.
            scratch = store.home_dir / "tmp" / "compile_mindspec"
            scratch.mkdir(parents=True, exist_ok=True)
            transcripts_dir = store.home_dir / "mindspec" / "transcripts"

            base_template = store.load_base()
            base_template["values_text"] = values

            llm = MiLlm(project_root=scratch, transcripts_dir=transcripts_dir)
            prompt = compile_mindspec_prompt(values_text=values, base_template=base_template)
            try:
                compiled = llm.call(schema_filename="compile_mindspec.json", prompt=prompt, tag="compile_mindspec").obj
            except Exception as e:
                print(f"compile_mindspec failed; falling back to defaults. error={e}", file=sys.stderr)

        if compiled is None:
            store.write_base_values(values_text=values)
            compiled = store.load_base()
        else:
            if not args.dry_run:
                store.write_base(compiled)

        if args.show or args.dry_run:
            vs = compiled.get("values_summary") or []
            if isinstance(vs, list) and any(str(x).strip() for x in vs):
                print("values_summary:")
                for x in vs:
                    x = str(x).strip()
                    if x:
                        print(f"- {x}")
            dp = compiled.get("decision_procedure") or {}
            if isinstance(dp, dict):
                summary = str(dp.get("summary") or "").strip()
                mermaid = str(dp.get("mermaid") or "").strip()
                if summary:
                    print("\ndecision_procedure.summary:\n" + summary)
                if mermaid:
                    print("\ndecision_procedure.mermaid:\n" + mermaid)

        if args.dry_run:
            print("(dry-run) did not write base MindSpec.")
            return 0

        print(f"Wrote base MindSpec to {store.base_path}")
        return 0

    if args.cmd == "run":
        result = run_autopilot(
            task=args.task,
            project_root=args.cd,
            home_dir=args.home,
            max_batches=args.max_batches,
        )
        if args.show:
            print(result.render_text())
        return 0 if result.status == "done" else 1

    if args.cmd == "last":
        project_root = Path(args.cd).resolve()
        home = Path(args.home) if args.home else default_home_dir()
        pp = ProjectPaths(home_dir=home, project_root=project_root)

        bundle = load_last_batch_bundle(pp.evidence_log_path)
        codex_input = bundle.get("codex_input") if isinstance(bundle.get("codex_input"), dict) else None
        evidence_item = bundle.get("evidence_item") if isinstance(bundle.get("evidence_item"), dict) else None

        transcript_path = ""
        if codex_input and isinstance(codex_input.get("transcript_path"), str):
            transcript_path = codex_input["transcript_path"]
        elif evidence_item and isinstance(evidence_item.get("codex_transcript_ref"), str):
            transcript_path = evidence_item["codex_transcript_ref"]

        last_msg = ""
        if transcript_path:
            last_msg = last_agent_message_from_transcript(Path(transcript_path))

        mi_input_text = (codex_input.get("input") if codex_input else "") or ""
        codex_last_text = last_msg or ""

        evidence_item_out = evidence_item or {}
        if args.redact:
            mi_input_text = redact_text(mi_input_text)
            codex_last_text = redact_text(codex_last_text)
            if isinstance(evidence_item_out, dict):
                for k in ("facts", "results", "unknowns", "risk_signals"):
                    v = evidence_item_out.get(k)
                    if isinstance(v, list):
                        evidence_item_out[k] = [redact_text(str(x)) for x in v]

        out = {
            "project_root": str(project_root),
            "project_dir": str(pp.project_dir),
            "evidence_log": str(pp.evidence_log_path),
            "batch_id": bundle.get("batch_id") or "",
            "thread_id": bundle.get("thread_id") or "",
            "hands_transcript": transcript_path,
            "mi_input": mi_input_text,
            "codex_last_message": codex_last_text,
            "evidence_item": evidence_item_out,
            "check_plan": (bundle.get("check_plan") or {}) if isinstance(bundle.get("check_plan"), dict) else {},
            "auto_answer": (bundle.get("auto_answer") or {}) if isinstance(bundle.get("auto_answer"), dict) else {},
            "risk_event": (bundle.get("risk_event") or {}) if isinstance(bundle.get("risk_event"), dict) else {},
            "loop_guard": (bundle.get("loop_guard") or {}) if isinstance(bundle.get("loop_guard"), dict) else {},
        }

        if args.json:
            print(json.dumps(out, indent=2, sort_keys=True))
            return 0

        print(f"thread_id={out['thread_id']} batch_id={out['batch_id']}")
        print(f"project_dir={out['project_dir']}")
        print(f"evidence_log={out['evidence_log']}")
        if transcript_path:
            print(f"hands_transcript={transcript_path}")
        if out["mi_input"].strip():
            print("\nmi_input:\n" + out["mi_input"].strip())
        if out["codex_last_message"].strip():
            print("\ncodex_last_message:\n" + out["codex_last_message"].strip())
        if isinstance(evidence_item_out, dict) and evidence_item_out:
            facts = evidence_item_out.get("facts") if isinstance(evidence_item_out.get("facts"), list) else []
            results = evidence_item_out.get("results") if isinstance(evidence_item_out.get("results"), list) else []
            unknowns = evidence_item_out.get("unknowns") if isinstance(evidence_item_out.get("unknowns"), list) else []
            if facts:
                print("\nfacts:")
                for x in facts[:8]:
                    xs = str(x).strip()
                    if xs:
                        print(f"- {xs}")
            if results:
                print("\nresults:")
                for x in results[:8]:
                    xs = str(x).strip()
                    if xs:
                        print(f"- {xs}")
            if unknowns:
                print("\nunknowns:")
                for x in unknowns[:8]:
                    xs = str(x).strip()
                    if xs:
                        print(f"- {xs}")
        return 0

    if args.cmd == "evidence":
        project_root = Path(args.cd).resolve()
        home = Path(args.home) if args.home else default_home_dir()
        pp = ProjectPaths(home_dir=home, project_root=project_root)
        if args.evidence_cmd == "tail":
            if args.raw:
                for line in tail_raw_lines(pp.evidence_log_path, args.lines):
                    print(redact_text(line) if args.redact else line)
                return 0
            for obj in tail_json_objects(pp.evidence_log_path, args.lines):
                s = summarize_evidence_record(obj)
                print(redact_text(s) if args.redact else s)
            return 0

    if args.cmd == "transcript":
        project_root = Path(args.cd).resolve()
        home = Path(args.home) if args.home else default_home_dir()
        pp = ProjectPaths(home_dir=home, project_root=project_root)
        if args.tr_cmd == "show":
            if args.path:
                tp = Path(args.path).expanduser()
            else:
                subdir = "mind" if args.mind else "hands"
                tdir = pp.transcripts_dir / subdir
                files = sorted([p for p in tdir.glob("*.jsonl") if p.is_file()])
                tp = files[-1] if files else Path("")
            if not tp or not str(tp):
                print("No transcript found.", file=sys.stderr)
                return 2
            if not tp.exists():
                print(f"Transcript not found: {tp}", file=sys.stderr)
                return 2

            lines = tail_raw_lines(tp, args.lines)
            print(str(tp))
            if args.jsonl:
                for line in lines:
                    print(redact_text(line) if args.redact else line)
                return 0

            for line in lines:
                try:
                    rec = json.loads(line)
                except Exception:
                    print(line)
                    continue
                if not isinstance(rec, dict):
                    print(line)
                    continue
                ts = str(rec.get("ts") or "")
                stream = str(rec.get("stream") or "")
                payload = rec.get("line")
                payload_s = str(payload) if payload is not None else ""
                if args.redact:
                    payload_s = redact_text(payload_s)
                print(f"{ts} {stream} {payload_s}")
            return 0

    if args.cmd == "learned":
        project_root = Path(args.cd).resolve()
        if args.learned_cmd == "list":
            entries = store.list_learned_entries(project_root)
            if not entries:
                print("(no learned entries)")
                return 0
            for e in entries:
                src = e.get("_source")
                entry_id = e.get("id")
                action = e.get("action") or "add"
                text = e.get("text") or ""
                print(f"{src} {entry_id} {action} {text}".strip())
            return 0
        if args.learned_cmd == "disable":
            store.disable_learned(project_root=project_root, scope=args.scope, target_id=args.id, rationale=args.rationale)
            print(f"Disabled {args.id} (scope={args.scope}) for project={project_root}")
            return 0

    return 2
