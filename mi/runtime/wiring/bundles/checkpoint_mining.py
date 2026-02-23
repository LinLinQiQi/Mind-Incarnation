from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mi.runtime import prompts as P
import mi.runtime.wiring as W
from mi.memory.ingest import thoughtdb_node_item
from mi.thoughtdb import claim_signature
from mi.workflows import load_workflow_candidates, new_workflow_id, write_workflow_candidates
from mi.workflows.hosts import sync_hosts_from_overlay
from mi.workflows.preferences import load_preference_candidates, preference_signature, write_preference_candidates


@dataclass(frozen=True)
class CheckpointMiningWiringBundle:
    """Checkpoint + mining wiring bundle (behavior-preserving)."""

    run_checkpoint_pipeline: Callable[..., Any]


def build_checkpoint_mining_wiring_bundle(
    *,
    checkpoint_enabled: bool,
    wf_auto_mine: bool,
    pref_auto_mine: bool,
    tdb_enabled: bool,
    tdb_auto_mine: bool,
    tdb_auto_nodes: bool,
    tdb_min_conf: float,
    tdb_max_claims: int,
    wf_cfg: dict[str, Any],
    pref_cfg: dict[str, Any],
    task: str,
    hands_provider: str,
    runtime_cfg_for_prompts: Callable[[], dict[str, Any]],
    overlay: dict[str, Any],
    evidence_window: list[dict[str, Any]],
    project_paths: Any,
    state_warnings: list[dict[str, Any]],
    flush_state_warnings: Callable[[], None],
    wf_registry: Any,
    wf_store: Any,
    mem: Any,
    tdb: Any,
    now_ts: Callable[[], str],
    truncate: Callable[[str, int], str],
    thread_id_getter: Callable[[], str],
    segment_id_getter: Callable[[], str],
    executed_batches_getter: Callable[[], int],
    status_getter: Callable[[], str],
    notes_getter: Callable[[], str],
    wf_sigs_counted_in_run: set[str],
    pref_sigs_counted_in_run: set[str],
    build_decide_context: Callable[..., Any],
    mind_call: Callable[..., Any],
    evidence_append: Callable[[dict[str, Any]], Any],
    handle_learn_suggested: Callable[..., list[str]],
    new_segment_state: Callable[..., dict[str, Any]],
) -> CheckpointMiningWiringBundle:
    """Build checkpoint + mining wiring and expose a checkpoint runner closure."""

    def _enabled_effective_workflows() -> list[dict[str, Any]]:
        workflows = wf_registry.enabled_workflows_effective(overlay=overlay) or []
        return [{k: v for k, v in w.items() if k != "_mi_scope"} for w in workflows if isinstance(w, dict)]

    def _sync_hosts(workflows: list[dict[str, Any]]) -> dict[str, Any]:
        return sync_hosts_from_overlay(
            overlay=overlay,
            project_id=project_paths.project_id,
            workflows=workflows,
            warnings=state_warnings,
        )

    workflow_mining_wiring = W.WorkflowMiningWiringDeps(
        enabled=bool(wf_auto_mine),
        executed_batches_getter=executed_batches_getter,
        wf_cfg=wf_cfg if isinstance(wf_cfg, dict) else {},
        status_getter=status_getter,
        notes_getter=notes_getter,
        task=task,
        hands_provider=hands_provider,
        runtime_cfg_getter=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        thread_id_getter=thread_id_getter,
        wf_sigs_counted_in_run=wf_sigs_counted_in_run,
        build_decide_context=build_decide_context,
        suggest_workflow_prompt_builder=P.suggest_workflow_prompt,
        mind_call=mind_call,
        evidence_append=evidence_append,
        load_workflow_candidates=lambda: load_workflow_candidates(project_paths, warnings=state_warnings),
        write_workflow_candidates=lambda obj: write_workflow_candidates(project_paths, obj),
        flush_state_warnings=flush_state_warnings,
        write_workflow=wf_store.write,
        new_workflow_id=new_workflow_id,
        enabled_effective_workflows=_enabled_effective_workflows,
        sync_hosts=_sync_hosts,
        now_ts=now_ts,
    )

    preference_mining_wiring = W.PreferenceMiningWiringDeps(
        enabled=bool(pref_auto_mine),
        executed_batches_getter=executed_batches_getter,
        pref_cfg=pref_cfg if isinstance(pref_cfg, dict) else {},
        status_getter=status_getter,
        notes_getter=notes_getter,
        task=task,
        hands_provider=hands_provider,
        runtime_cfg_getter=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        thread_id_getter=thread_id_getter,
        project_id=str(project_paths.project_id or ""),
        pref_sigs_counted_in_run=pref_sigs_counted_in_run,
        build_decide_context=build_decide_context,
        mine_preferences_prompt_builder=P.mine_preferences_prompt,
        mind_call=mind_call,
        evidence_append=evidence_append,
        load_preference_candidates=lambda: load_preference_candidates(project_paths, warnings=state_warnings),
        write_preference_candidates=lambda obj: write_preference_candidates(project_paths, obj),
        flush_state_warnings=flush_state_warnings,
        existing_signature_map=lambda scope: tdb.existing_signature_map(scope=scope),
        claim_signature_fn=claim_signature,
        preference_signature_fn=preference_signature,
        handle_learn_suggested=handle_learn_suggested,
        now_ts=now_ts,
    )

    claim_mining_wiring = W.ClaimMiningWiringDeps(
        enabled=bool(tdb_auto_mine),
        executed_batches_getter=executed_batches_getter,
        max_claims=int(tdb_max_claims),
        min_confidence=float(tdb_min_conf),
        status_getter=status_getter,
        notes_getter=notes_getter,
        task=task,
        hands_provider=hands_provider,
        runtime_cfg_getter=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        thread_id_getter=thread_id_getter,
        segment_id_getter=segment_id_getter,
        build_decide_context=build_decide_context,
        mine_claims_prompt_builder=P.mine_claims_prompt,
        mind_call=mind_call,
        apply_mined_output=tdb.apply_mined_output,
        evidence_append=evidence_append,
        now_ts=now_ts,
    )

    node_materialize_wiring = W.NodeMaterializeWiringDeps(
        enabled=bool(tdb_enabled) and bool(tdb_auto_nodes),
        task=task,
        now_ts=now_ts,
        truncate=truncate,
        project_id=str(project_paths.project_id or ""),
        nodes_path=project_paths.thoughtdb_nodes_path,
        thread_id_getter=thread_id_getter,
        segment_id_getter=segment_id_getter,
        append_node_create=tdb.append_node_create,
        append_edge=tdb.append_edge,
        upsert_memory_items=mem.upsert_items,
        build_index_item=thoughtdb_node_item,
        evidence_append=evidence_append,
    )

    def _mine_workflow_from_segment(*, seg_evidence: list[dict[str, Any]], base_batch_id: str, source: str) -> None:
        W.mine_workflow_from_segment_wired(
            seg_evidence=seg_evidence,
            base_batch_id=base_batch_id,
            source=source,
            deps=workflow_mining_wiring,
        )

    def _mine_preferences_from_segment(*, seg_evidence: list[dict[str, Any]], base_batch_id: str, source: str) -> None:
        W.mine_preferences_from_segment_wired(
            seg_evidence=seg_evidence,
            base_batch_id=base_batch_id,
            source=source,
            deps=preference_mining_wiring,
        )

    def _mine_claims_from_segment(*, seg_evidence: list[dict[str, Any]], base_batch_id: str, source: str) -> None:
        W.mine_claims_from_segment_wired(
            seg_evidence=seg_evidence,
            base_batch_id=base_batch_id,
            source=source,
            deps=claim_mining_wiring,
        )

    def _materialize_nodes_from_checkpoint(
        *,
        seg_evidence: list[dict[str, Any]],
        snapshot_rec: dict[str, Any] | None,
        base_batch_id: str,
        checkpoint_kind: str,
        status_hint: str,
        planned_next_input: str,
        note: str,
    ) -> None:
        W.materialize_nodes_from_checkpoint_wired(
            seg_evidence=seg_evidence,
            snapshot_rec=snapshot_rec,
            base_batch_id=base_batch_id,
            checkpoint_kind=checkpoint_kind,
            status_hint=status_hint,
            planned_next_input=planned_next_input,
            note=note,
            deps=node_materialize_wiring,
        )

    checkpoint_wiring = W.CheckpointWiringDeps(
        checkpoint_enabled=bool(checkpoint_enabled),
        task=task,
        hands_provider=hands_provider,
        runtime_cfg=runtime_cfg_for_prompts,
        project_overlay=overlay if isinstance(overlay, dict) else {},
        evidence_window=evidence_window,
        thread_id_getter=thread_id_getter,
        build_decide_context=build_decide_context,
        checkpoint_decide_prompt_builder=P.checkpoint_decide_prompt,
        mind_call=mind_call,
        evidence_append=evidence_append,
        mine_workflow_from_segment=_mine_workflow_from_segment,
        mine_preferences_from_segment=_mine_preferences_from_segment,
        mine_claims_from_segment=_mine_claims_from_segment,
        materialize_snapshot=mem.materialize_snapshot,
        materialize_nodes_from_checkpoint=_materialize_nodes_from_checkpoint,
        new_segment_state=new_segment_state,
        now_ts=now_ts,
        truncate=truncate,
    )

    def run_checkpoint_pipeline(**kwargs: Any) -> Any:
        return W.run_checkpoint_pipeline_wired(
            segment_state=kwargs.get("segment_state") if isinstance(kwargs.get("segment_state"), dict) else {},
            segment_records=kwargs.get("segment_records") if isinstance(kwargs.get("segment_records"), list) else [],
            last_checkpoint_key=str(kwargs.get("last_checkpoint_key") or ""),
            batch_id=str(kwargs.get("batch_id") or ""),
            planned_next_input=str(kwargs.get("planned_next_input") or ""),
            status_hint=str(kwargs.get("status_hint") or ""),
            note=str(kwargs.get("note") or ""),
            deps=checkpoint_wiring,
        )

    return CheckpointMiningWiringBundle(run_checkpoint_pipeline=run_checkpoint_pipeline)
