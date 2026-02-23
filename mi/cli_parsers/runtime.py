from __future__ import annotations

from typing import Any


def add_runtime_subparsers(*, sub: Any) -> None:
    p_run = sub.add_parser("run", help="Run MI batch autopilot (Hands configured via mi config).")
    p_run.add_argument("task", nargs="+", help="User task for Hands to execute (multi-word; quotes optional).")
    p_run.add_argument(
        "--cd",
        default="",
        help="Project root for the Hands run (default: infer from cwd; git toplevel when available).",
    )
    p_run.add_argument(
        "--max-batches",
        type=int,
        default=8,
        help="Maximum number of Hands batches before stopping.",
    )
    p_run.add_argument(
        "--continue-hands",
        action="store_true",
        help="Try to resume the last stored Hands thread/session id across separate `mi run` invocations (best-effort).",
    )
    p_run.add_argument(
        "--reset-hands",
        action="store_true",
        help="Clear the stored Hands thread/session id for this project before running (forces a fresh Hands session).",
    )
    p_run.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress live output and the end summary (useful for scripts/CI).",
    )
    p_run.add_argument(
        "--hands-raw",
        action="store_true",
        help="Print raw Hands stdout/stderr as captured (Codex: JSON event lines) instead of rendered output.",
    )
    p_run.add_argument(
        "--no-mi-prompt",
        action="store_true",
        help="Do not print the full MI->Hands prompt (still persisted to EvidenceLog).",
    )
    p_run.add_argument(
        "--redact",
        action="store_true",
        help="Best-effort redact common secret/token patterns in live display output (stored logs remain unchanged).",
    )
    p_run.add_argument(
        "--why",
        action="store_true",
        help="Opt-in: run one WhyTrace at run end (writes kind=why_trace; may materialize depends_on edges).",
    )

    p_status = sub.add_parser("status", help="Show everyday status for the current project (read-only).")
    p_status.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_status.add_argument("--json", action="store_true", help="Print as JSON.")
    p_status.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

    p_show = sub.add_parser(
        "show",
        help="Show an MI resource by id (ev_/cl_/nd_/wf_/ed_) or a transcript .jsonl path (best-effort).",
    )
    p_show.add_argument("ref", help="Resource id (ev_/cl_/nd_/wf_/ed_) or transcript path.")
    p_show.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_show.add_argument(
        "--global",
        dest="show_global",
        action="store_true",
        help="For ev_... refs: search global EvidenceLog only (skip project fallback).",
    )
    p_show.add_argument("-n", "--lines", type=int, default=200, help="Number of transcript lines to show when ref is a .jsonl path.")
    p_show.add_argument("--jsonl", action="store_true", help="When showing a transcript path: print raw JSONL lines.")
    p_show.add_argument("--json", action="store_true", help="Print as JSON when possible.")
    p_show.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")

    p_tail = sub.add_parser("tail", help="Tail recent MI activity (EvidenceLog or transcripts).")
    p_tail.add_argument(
        "target",
        nargs="?",
        default="evidence",
        choices=["evidence", "hands", "mind"],
        help="What to tail (default: evidence).",
    )
    p_tail.add_argument("--cd", default="", help="Project root used to locate MI artifacts.")
    p_tail.add_argument(
        "--global",
        dest="tail_global",
        action="store_true",
        help="For evidence: tail the global EvidenceLog instead of the project one.",
    )
    p_tail.add_argument(
        "-n",
        "--lines",
        type=int,
        default=None,
        help="Number of records/lines to show (evidence defaults to 20; transcripts default to 200).",
    )
    p_tail.add_argument("--raw", action="store_true", help="For evidence: print raw JSONL lines.")
    p_tail.add_argument("--json", action="store_true", help="For evidence: print parsed JSON records as a JSON array.")
    p_tail.add_argument("--jsonl", action="store_true", help="For transcripts: print stored JSONL lines (no pretty formatting).")
    p_tail.add_argument("--redact", action="store_true", help="Redact common secret/token patterns for display.")


__all__ = ["add_runtime_subparsers"]

