from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ...core.config import load_config
from ...core.paths import GlobalPaths, ProjectPaths, default_home_dir
from ...core.redact import redact_text
from ...core.storage import ensure_dir, now_rfc3339
from ..evidence import EvidenceWriter, new_run_id
from ...memory.facade import MemoryFacade
from ...project.overlay_store import load_project_overlay, write_project_overlay
from ...providers.codex_runner import run_codex_exec, run_codex_resume
from ...providers.llm import MiLlm
from ...thoughtdb import ThoughtDbStore
from ...thoughtdb.app_service import ThoughtDbApplicationService
from ...workflows import (
    GlobalWorkflowStore,
    WorkflowRegistry,
    WorkflowStore,
    render_workflow_markdown,
)
from ...runtime.autopilot.workflow_cursor import match_workflow_for_task, workflow_step_ids
from ...runtime.autopilot.run_context import RunSession


@dataclass(frozen=True)
class BootstrappedAutopilotRun:
    project_path: Path
    home: Path
    runtime_cfg: dict[str, Any]
    state_warnings: list[dict[str, Any]]
    overlay: dict[str, Any]
    hands_state: dict[str, Any]
    workflow_run: dict[str, Any]
    refresh_overlay_refs: Callable[[], None]
    cur_provider: str
    project_paths: ProjectPaths
    wf_store: WorkflowStore
    wf_registry: WorkflowRegistry
    mem: MemoryFacade
    tdb: ThoughtDbStore
    tdb_app: ThoughtDbApplicationService
    evw: EvidenceWriter
    llm: Any
    hands_exec: Any
    hands_resume: Any
    run_session: RunSession
    live_enabled: bool
    emit_prefixed: Callable[[str, str], None]
    evidence_window: list[dict[str, Any]]
    thread_id: str | None
    resumed_from_overlay: bool
    next_input: str
    matched_workflow: dict[str, Any] | None


def bootstrap_autopilot_run(
    *,
    task: str,
    project_root: str,
    home_dir: str | None,
    hands_provider: str,
    continue_hands: bool,
    reset_hands: bool,
    llm: Any | None,
    hands_exec: Any | None,
    hands_resume: Any,
    hands_resume_default_sentinel: object,
    live: bool,
    quiet: bool,
    redact: bool,
    read_user_answer: Callable[[str], str],
) -> BootstrappedAutopilotRun:
    """Bootstrap MI runtime state + durable stores for `mi run` (behavior-preserving).

    Note: overlay/hands_state/workflow_run dict identities are preserved across refreshes.
    Callers may hold references to these dicts and see in-place updates after
    `refresh_overlay_refs()` is invoked by flows.
    """

    project_path = Path(project_root).resolve()
    home = Path(home_dir).expanduser().resolve() if home_dir else default_home_dir()
    cfg = load_config(home)
    runtime_cfg = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
    state_warnings: list[dict[str, Any]] = []

    overlay: dict[str, Any] = {}
    hands_state: dict[str, Any] = {}
    workflow_run: dict[str, Any] = {}

    def _refresh_overlay_refs() -> None:
        loaded = load_project_overlay(home_dir=home, project_root=project_path, warnings=state_warnings)
        loaded_overlay = loaded if isinstance(loaded, dict) else {}

        # Preserve dict identity so other wiring can hold references.
        overlay.clear()
        overlay.update(loaded_overlay)

        loaded_hs = overlay.get("hands_state")
        if isinstance(loaded_hs, dict):
            hands_state.clear()
            hands_state.update(loaded_hs)
        else:
            hands_state.clear()
        overlay["hands_state"] = hands_state

        loaded_wr = overlay.get("workflow_run")
        if isinstance(loaded_wr, dict):
            workflow_run.clear()
            workflow_run.update(loaded_wr)
        else:
            workflow_run.clear()
        overlay["workflow_run"] = workflow_run

    _refresh_overlay_refs()

    cur_provider = (hands_provider or str(hands_state.get("provider") or "")).strip()
    if reset_hands:
        hands_state["provider"] = cur_provider
        hands_state["thread_id"] = ""
        hands_state["updated_ts"] = now_rfc3339()
        # Reset any best-effort workflow cursor that may be tied to the previous Hands thread.
        workflow_run.clear()
        overlay["workflow_run"] = workflow_run
        write_project_overlay(home_dir=home, project_root=project_path, overlay=overlay)
        _refresh_overlay_refs()

    project_paths = ProjectPaths(home_dir=home, project_root=project_path)
    ensure_dir(project_paths.project_dir)
    ensure_dir(project_paths.transcripts_dir)

    wf_store = WorkflowStore(project_paths)
    wf_global_store = GlobalWorkflowStore(GlobalPaths(home_dir=home))
    wf_registry = WorkflowRegistry(project_store=wf_store, global_store=wf_global_store)
    mem = MemoryFacade(home_dir=home, project_paths=project_paths, runtime_cfg=runtime_cfg)
    mem.ensure_structured_ingested()
    tdb = ThoughtDbStore(home_dir=home, project_paths=project_paths)
    tdb_app = ThoughtDbApplicationService(tdb=tdb, project_paths=project_paths, mem=mem.service)
    evw = EvidenceWriter(path=project_paths.evidence_log_path, run_id=new_run_id("run"))

    if llm is None:
        llm = MiLlm(project_root=project_path, transcripts_dir=project_paths.transcripts_dir)
    if hands_exec is None:
        hands_exec = run_codex_exec
    if hands_resume is hands_resume_default_sentinel:
        hands_resume = run_codex_resume

    live_enabled = bool(live) and (not bool(quiet))

    def _emit_prefixed(prefix: str, text: str) -> None:
        if not live_enabled:
            return
        s = str(text or "")
        if redact:
            s = redact_text(s)
        lines = s.splitlines() if s else [""]
        for line in lines:
            if line:
                print(f"{prefix} {line}", flush=True)
            else:
                print(prefix, flush=True)

    run_session = RunSession(
        home=home,
        project_path=project_path,
        project_paths=project_paths,
        runtime_cfg=(runtime_cfg if isinstance(runtime_cfg, dict) else {}),
        llm=llm,
        hands_exec=hands_exec,
        hands_resume=hands_resume,
        evw=evw,
        tdb=tdb,
        mem=mem,
        wf_registry=wf_registry,
        emit=_emit_prefixed,
        read_user_answer=read_user_answer,
        now_ts=now_rfc3339,
    )

    evidence_window: list[dict[str, Any]] = []
    thread_id: str | None = None
    resumed_from_overlay = False
    if continue_hands and not reset_hands and hands_resume is not None:
        prev_tid = str(hands_state.get("thread_id") or "").strip()
        prev_provider = str(hands_state.get("provider") or "").strip()
        if prev_tid and prev_tid != "unknown" and (not cur_provider or not prev_provider or prev_provider == cur_provider):
            thread_id = prev_tid
            resumed_from_overlay = True

    # Default: do not carry an "active" workflow cursor across runs unless we are explicitly continuing the same Hands session.
    if bool(workflow_run.get("active", False)) and not bool(resumed_from_overlay):
        workflow_run.clear()
        overlay["workflow_run"] = workflow_run
        write_project_overlay(home_dir=home, project_root=project_path, overlay=overlay)

    next_input: str = task

    # Workflow trigger routing (effective): if an enabled workflow (project or global) matches the task,
    # inject it into the very first batch input (lightweight; no step slicing).
    matched = match_workflow_for_task(task_text=task, workflows=wf_registry.enabled_workflows_effective(overlay=overlay))
    if matched:
        wid = str(matched.get("id") or "").strip()
        name = str(matched.get("name") or "").strip()
        trig = matched.get("trigger") if isinstance(matched.get("trigger"), dict) else {}
        pat = str(trig.get("pattern") or "").strip()
        # Best-effort workflow cursor: internal only. It does NOT impose step-by-step reporting.
        # The cursor is used to provide next-step context to Mind prompts.
        step_ids = workflow_step_ids(matched)
        workflow_run.clear()
        workflow_run.update(
            {
                "version": "v1",
                "active": True,
                "workflow_id": wid,
                "workflow_name": name,
                "thread_id": str(thread_id or hands_state.get("thread_id") or ""),
                "started_ts": now_rfc3339(),
                "updated_ts": now_rfc3339(),
                "completed_step_ids": [],
                "next_step_id": step_ids[0] if step_ids else "",
                "last_batch_id": "b0.workflow_trigger",
                "last_confidence": 0.0,
                "last_notes": f"triggered: task_contains pattern={pat}",
            }
        )
        overlay["workflow_run"] = workflow_run
        write_project_overlay(home_dir=home, project_root=project_path, overlay=overlay)
        injected = "\n".join(
            [
                "[MI Workflow Triggered]",
                "A reusable workflow matches this task.",
                "- Use it as guidance; you do NOT need to report step-by-step.",
                "- If network/install/push/publish is not clearly safe per values, pause and ask.",
                "",
                render_workflow_markdown(matched),
                "",
                "User task:",
                task.strip(),
            ]
        ).strip()
        next_input = injected
        wf_trig = evw.append(
            {
                "kind": "workflow_trigger",
                "batch_id": "b0.workflow_trigger",
                "ts": now_rfc3339(),
                "thread_id": thread_id or "",
                "workflow_id": wid,
                "workflow_name": name,
                "trigger_mode": str(trig.get("mode") or ""),
                "trigger_pattern": pat,
            }
        )
        evidence_window.append(
            {
                "kind": "workflow_trigger",
                "batch_id": "b0.workflow_trigger",
                "event_id": (wf_trig if isinstance(wf_trig, dict) else {}).get("event_id"),
                "workflow_id": wid,
                "workflow_name": name,
                "trigger_mode": str(trig.get("mode") or ""),
                "trigger_pattern": pat,
            }
        )

    return BootstrappedAutopilotRun(
        project_path=project_path,
        home=home,
        runtime_cfg=runtime_cfg if isinstance(runtime_cfg, dict) else {},
        state_warnings=state_warnings,
        overlay=overlay,
        hands_state=hands_state,
        workflow_run=workflow_run,
        refresh_overlay_refs=_refresh_overlay_refs,
        cur_provider=cur_provider,
        project_paths=project_paths,
        wf_store=wf_store,
        wf_registry=wf_registry,
        mem=mem,
        tdb=tdb,
        tdb_app=tdb_app,
        evw=evw,
        llm=llm,
        hands_exec=hands_exec,
        hands_resume=hands_resume,
        run_session=run_session,
        live_enabled=live_enabled,
        emit_prefixed=_emit_prefixed,
        evidence_window=evidence_window,
        thread_id=thread_id,
        resumed_from_overlay=resumed_from_overlay,
        next_input=next_input,
        matched_workflow=matched if isinstance(matched, dict) else None,
    )
