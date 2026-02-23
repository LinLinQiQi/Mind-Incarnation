from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..runner_helpers import dict_or_empty


@dataclass(frozen=True)
class PhaseDicts:
    """Normalized dict inputs for phase wiring (behavior-preserving).

    Many wiring bundles accept dict-shaped objects (overlay/workflow/runtime cfg).
    This helper keeps normalization consistent and reduces drift.
    """

    overlay: dict[str, Any]
    workflow_run: dict[str, Any]
    wf_cfg: dict[str, Any]
    pref_cfg: dict[str, Any]
    runtime_cfg: dict[str, Any]


def normalize_phase_dicts(
    *,
    overlay: Any,
    workflow_run: Any,
    wf_cfg: Any,
    pref_cfg: Any,
    runtime_cfg: Any,
) -> PhaseDicts:
    return PhaseDicts(
        overlay=dict_or_empty(overlay),
        workflow_run=dict_or_empty(workflow_run),
        wf_cfg=dict_or_empty(wf_cfg),
        pref_cfg=dict_or_empty(pref_cfg),
        runtime_cfg=dict_or_empty(runtime_cfg),
    )


__all__ = ["PhaseDicts", "normalize_phase_dicts"]

