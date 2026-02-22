from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ..autopilot.checkpoint_mining import (
    PreferenceMiningDeps,
    WorkflowMiningDeps,
    mine_preferences_from_segment as run_preference_mining,
    mine_workflow_from_segment as run_workflow_mining,
)
from ..autopilot.claim_mining_flow import ClaimMiningDeps, mine_claims_from_segment as run_claim_mining
from ..autopilot.node_materialize import NodeMaterializeDeps, materialize_nodes_from_checkpoint


@dataclass(frozen=True)
class WorkflowMiningWiringDeps:
    enabled: bool
    executed_batches_getter: Callable[[], int]
    wf_cfg: dict[str, Any]
    status_getter: Callable[[], str]
    notes_getter: Callable[[], str]
    task: str
    hands_provider: str
    mindspec_base_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    thread_id_getter: Callable[[], str]
    wf_sigs_counted_in_run: set[str]

    build_decide_context: Callable[..., Any]
    suggest_workflow_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[dict[str, Any] | None, str, str]]
    evidence_append: Callable[[dict[str, Any]], Any]
    load_workflow_candidates: Callable[[], dict[str, Any]]
    write_workflow_candidates: Callable[[dict[str, Any]], None]
    flush_state_warnings: Callable[[], None]
    write_workflow: Callable[[dict[str, Any]], None]
    new_workflow_id: Callable[[], str]
    enabled_effective_workflows: Callable[[], list[dict[str, Any]]]
    sync_hosts: Callable[[list[dict[str, Any]]], dict[str, Any]]
    now_ts: Callable[[], str]


def mine_workflow_from_segment_wired(
    *,
    seg_evidence: list[dict[str, Any]],
    base_batch_id: str,
    source: str,
    deps: WorkflowMiningWiringDeps,
) -> None:
    run_workflow_mining(
        enabled=bool(deps.enabled),
        executed_batches=int(deps.executed_batches_getter()),
        wf_cfg=deps.wf_cfg if isinstance(deps.wf_cfg, dict) else {},
        seg_evidence=seg_evidence if isinstance(seg_evidence, list) else [],
        base_batch_id=str(base_batch_id or ""),
        source=str(source or ""),
        status=str(deps.status_getter() or ""),
        notes=str(deps.notes_getter() or ""),
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        mindspec_base=deps.mindspec_base_getter() if callable(deps.mindspec_base_getter) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        thread_id=str(deps.thread_id_getter() or ""),
        wf_sigs_counted_in_run=deps.wf_sigs_counted_in_run,
        deps=WorkflowMiningDeps(
            build_decide_context=deps.build_decide_context,
            suggest_workflow_prompt_builder=deps.suggest_workflow_prompt_builder,
            mind_call=deps.mind_call,
            evidence_append=deps.evidence_append,
            load_workflow_candidates=deps.load_workflow_candidates,
            write_workflow_candidates=deps.write_workflow_candidates,
            flush_state_warnings=deps.flush_state_warnings,
            write_workflow=deps.write_workflow,
            new_workflow_id=deps.new_workflow_id,
            enabled_effective_workflows=deps.enabled_effective_workflows,
            sync_hosts=deps.sync_hosts,
            now_ts=deps.now_ts,
        ),
    )


@dataclass(frozen=True)
class PreferenceMiningWiringDeps:
    enabled: bool
    executed_batches_getter: Callable[[], int]
    pref_cfg: dict[str, Any]
    status_getter: Callable[[], str]
    notes_getter: Callable[[], str]
    task: str
    hands_provider: str
    mindspec_base_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    thread_id_getter: Callable[[], str]
    project_id: str
    pref_sigs_counted_in_run: set[str]

    build_decide_context: Callable[..., Any]
    mine_preferences_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[dict[str, Any] | None, str, str]]
    evidence_append: Callable[[dict[str, Any]], Any]
    load_preference_candidates: Callable[[], dict[str, Any]]
    write_preference_candidates: Callable[[dict[str, Any]], None]
    flush_state_warnings: Callable[[], None]
    existing_signature_map: Callable[[str], dict[str, str]]
    claim_signature_fn: Callable[..., str]
    preference_signature_fn: Callable[..., str]
    handle_learn_suggested: Callable[..., list[str]]
    now_ts: Callable[[], str]


def mine_preferences_from_segment_wired(
    *,
    seg_evidence: list[dict[str, Any]],
    base_batch_id: str,
    source: str,
    deps: PreferenceMiningWiringDeps,
) -> None:
    run_preference_mining(
        enabled=bool(deps.enabled),
        executed_batches=int(deps.executed_batches_getter()),
        pref_cfg=deps.pref_cfg if isinstance(deps.pref_cfg, dict) else {},
        seg_evidence=seg_evidence if isinstance(seg_evidence, list) else [],
        base_batch_id=str(base_batch_id or ""),
        source=str(source or ""),
        status=str(deps.status_getter() or ""),
        notes=str(deps.notes_getter() or ""),
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        mindspec_base=deps.mindspec_base_getter() if callable(deps.mindspec_base_getter) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        thread_id=str(deps.thread_id_getter() or ""),
        project_id=str(deps.project_id or ""),
        pref_sigs_counted_in_run=deps.pref_sigs_counted_in_run,
        deps=PreferenceMiningDeps(
            build_decide_context=deps.build_decide_context,
            mine_preferences_prompt_builder=deps.mine_preferences_prompt_builder,
            mind_call=deps.mind_call,
            evidence_append=deps.evidence_append,
            load_preference_candidates=deps.load_preference_candidates,
            write_preference_candidates=deps.write_preference_candidates,
            flush_state_warnings=deps.flush_state_warnings,
            existing_signature_map=deps.existing_signature_map,
            claim_signature_fn=deps.claim_signature_fn,
            preference_signature_fn=deps.preference_signature_fn,
            handle_learn_suggested=deps.handle_learn_suggested,
            now_ts=deps.now_ts,
        ),
    )


@dataclass(frozen=True)
class ClaimMiningWiringDeps:
    enabled: bool
    executed_batches_getter: Callable[[], int]
    max_claims: int
    min_confidence: float
    status_getter: Callable[[], str]
    notes_getter: Callable[[], str]
    task: str
    hands_provider: str
    mindspec_base_getter: Callable[[], dict[str, Any]]
    project_overlay: dict[str, Any]
    thread_id_getter: Callable[[], str]
    segment_id_getter: Callable[[], str]

    build_decide_context: Callable[..., Any]
    mine_claims_prompt_builder: Callable[..., str]
    mind_call: Callable[..., tuple[dict[str, Any] | None, str, str]]
    apply_mined_output: Callable[..., Any]
    evidence_append: Callable[[dict[str, Any]], Any]
    now_ts: Callable[[], str]


def mine_claims_from_segment_wired(
    *,
    seg_evidence: list[dict[str, Any]],
    base_batch_id: str,
    source: str,
    deps: ClaimMiningWiringDeps,
) -> None:
    run_claim_mining(
        enabled=bool(deps.enabled),
        executed_batches=int(deps.executed_batches_getter()),
        max_claims=int(deps.max_claims),
        min_confidence=float(deps.min_confidence),
        seg_evidence=seg_evidence if isinstance(seg_evidence, list) else [],
        base_batch_id=str(base_batch_id or ""),
        source=str(source or ""),
        status=str(deps.status_getter() or ""),
        notes=str(deps.notes_getter() or ""),
        task=str(deps.task or ""),
        hands_provider=str(deps.hands_provider or ""),
        mindspec_base=deps.mindspec_base_getter() if callable(deps.mindspec_base_getter) else {},
        project_overlay=deps.project_overlay if isinstance(deps.project_overlay, dict) else {},
        thread_id=str(deps.thread_id_getter() or ""),
        segment_id=str(deps.segment_id_getter() or ""),
        deps=ClaimMiningDeps(
            build_decide_context=deps.build_decide_context,
            mine_claims_prompt_builder=deps.mine_claims_prompt_builder,
            mind_call=deps.mind_call,
            apply_mined_output=deps.apply_mined_output,
            evidence_append=deps.evidence_append,
            now_ts=deps.now_ts,
        ),
    )


@dataclass(frozen=True)
class NodeMaterializeWiringDeps:
    enabled: bool
    task: str
    now_ts: Callable[[], str]
    truncate: Callable[[str, int], str]
    project_id: str
    nodes_path: Any
    thread_id_getter: Callable[[], str]
    segment_id_getter: Callable[[], str]

    append_node_create: Callable[..., Any]
    append_edge: Callable[..., Any]
    upsert_memory_items: Callable[..., Any]
    build_index_item: Callable[..., Any]
    evidence_append: Callable[[dict[str, Any]], Any]


def materialize_nodes_from_checkpoint_wired(
    *,
    seg_evidence: list[dict[str, Any]],
    snapshot_rec: dict[str, Any] | None,
    base_batch_id: str,
    checkpoint_kind: str,
    status_hint: str,
    planned_next_input: str,
    note: str,
    deps: NodeMaterializeWiringDeps,
) -> None:
    materialize_nodes_from_checkpoint(
        enabled=bool(deps.enabled),
        seg_evidence=seg_evidence if isinstance(seg_evidence, list) else [],
        snapshot_rec=snapshot_rec if isinstance(snapshot_rec, dict) else None,
        base_batch_id=str(base_batch_id or ""),
        checkpoint_kind=str(checkpoint_kind or ""),
        status_hint=str(status_hint or ""),
        planned_next_input=str(planned_next_input or ""),
        note=str(note or ""),
        deps=NodeMaterializeDeps(
            append_node_create=deps.append_node_create,
            append_edge=deps.append_edge,
            upsert_memory_items=deps.upsert_memory_items,
            build_index_item=deps.build_index_item,
            evidence_append=deps.evidence_append,
            now_ts=deps.now_ts,
            truncate=deps.truncate,
            project_id=str(deps.project_id or ""),
            nodes_path=deps.nodes_path,
            task=str(deps.task or ""),
            thread_id=str(deps.thread_id_getter() or ""),
            segment_id=str(deps.segment_id_getter() or ""),
        ),
    )

