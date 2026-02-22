from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...providers.interrupts import InterruptConfig


@dataclass(frozen=True)
class ParsedRuntimeFeatures:
    wf_auto_mine: bool
    pref_auto_mine: bool
    tdb_enabled: bool
    tdb_auto_mine: bool
    tdb_auto_nodes: bool
    tdb_min_conf: float
    tdb_max_claims: int
    auto_why_on_end: bool
    why_top_k: int
    why_min_write_conf: float
    why_write_edges: bool
    checkpoint_enabled: bool
    interrupt_cfg: InterruptConfig | None


def parse_runtime_features(*, runtime_cfg: dict[str, Any], why_trace_on_run_end: bool) -> ParsedRuntimeFeatures:
    """Parse runtime feature flags from config (behavior-preserving defaults + clamps)."""

    wf_cfg = runtime_cfg.get("workflows") if isinstance(runtime_cfg.get("workflows"), dict) else {}
    wf_auto_mine = bool(wf_cfg.get("auto_mine", True))

    pref_cfg = runtime_cfg.get("preference_mining") if isinstance(runtime_cfg.get("preference_mining"), dict) else {}
    pref_auto_mine = bool(pref_cfg.get("auto_mine", True))

    tdb_cfg = runtime_cfg.get("thought_db") if isinstance(runtime_cfg.get("thought_db"), dict) else {}
    tdb_enabled = bool(tdb_cfg.get("enabled", True))
    tdb_auto_mine = bool(tdb_cfg.get("auto_mine", True)) and bool(tdb_enabled)

    # Deterministic node materialization does not add mind calls; keep it separately controllable.
    tdb_auto_nodes = bool(tdb_cfg.get("auto_materialize_nodes", True)) and bool(tdb_enabled)

    try:
        tdb_min_conf = float(tdb_cfg.get("min_confidence", 0.9) or 0.9)
    except Exception:
        tdb_min_conf = 0.9
    tdb_min_conf = max(0.0, min(1.0, tdb_min_conf))

    try:
        tdb_max_claims = int(tdb_cfg.get("max_claims_per_checkpoint", 6) or 6)
    except Exception:
        tdb_max_claims = 6
    tdb_max_claims = max(0, min(20, tdb_max_claims))

    # Optional: automatic run-end WhyTrace (opt-in; one call per `mi run`).
    why_cfg = tdb_cfg.get("why_trace") if isinstance(tdb_cfg.get("why_trace"), dict) else {}
    auto_why_on_end = (bool(why_cfg.get("auto_on_run_end", False)) or bool(why_trace_on_run_end)) and bool(tdb_enabled)

    try:
        why_top_k = int(why_cfg.get("top_k", 12) or 12)
    except Exception:
        why_top_k = 12
    why_top_k = max(1, min(40, why_top_k))

    try:
        why_min_write_conf = float(why_cfg.get("min_write_confidence", 0.7) or 0.7)
    except Exception:
        why_min_write_conf = 0.7
    why_min_write_conf = max(0.0, min(1.0, why_min_write_conf))

    why_write_edges = bool(why_cfg.get("write_edges", True))

    checkpoint_enabled = bool(wf_auto_mine or pref_auto_mine or tdb_auto_mine or tdb_auto_nodes)

    intr = runtime_cfg.get("interrupt") if isinstance(runtime_cfg.get("interrupt"), dict) else {}
    intr_mode = str(intr.get("mode") or "off")
    intr_signals = intr.get("signal_sequence") or ["SIGINT", "SIGTERM", "SIGKILL"]
    intr_escalation = intr.get("escalation_ms") or [2000, 5000]
    interrupt_cfg = (
        InterruptConfig(mode=intr_mode, signal_sequence=[str(s) for s in intr_signals], escalation_ms=[int(x) for x in intr_escalation])
        if intr_mode in ("on_high_risk", "on_any_external")
        else None
    )

    return ParsedRuntimeFeatures(
        wf_auto_mine=bool(wf_auto_mine),
        pref_auto_mine=bool(pref_auto_mine),
        tdb_enabled=bool(tdb_enabled),
        tdb_auto_mine=bool(tdb_auto_mine),
        tdb_auto_nodes=bool(tdb_auto_nodes),
        tdb_min_conf=float(tdb_min_conf),
        tdb_max_claims=int(tdb_max_claims),
        auto_why_on_end=bool(auto_why_on_end),
        why_top_k=int(why_top_k),
        why_min_write_conf=float(why_min_write_conf),
        why_write_edges=bool(why_write_edges),
        checkpoint_enabled=bool(checkpoint_enabled),
        interrupt_cfg=interrupt_cfg,
    )

