import argparse
import os
import sys
from pathlib import Path

from .mindspec import MindSpecStore
from .llm import MiLlm
from .prompts import compile_mindspec_prompt
from .runner import run_autopilot


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

    args = parser.parse_args(argv)

    store = MindSpecStore(home_dir=args.home)

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
