"""Runtime wiring helpers (internal).

This package exists to keep mi/runtime/runner.py small and behavior-stable by
centralizing bootstrap/config parsing code in testable modules.
"""

from .bootstrap import BootstrappedAutopilotRun, bootstrap_autopilot_run
from .check_plan import CheckPlanWiringDeps, plan_checks_and_record_wired
from .checkpoints import CheckpointWiringDeps, run_checkpoint_pipeline_wired
from .mind_call import MindCaller
from .mining import (
    ClaimMiningWiringDeps,
    NodeMaterializeWiringDeps,
    PreferenceMiningWiringDeps,
    WorkflowMiningWiringDeps,
    materialize_nodes_from_checkpoint_wired,
    mine_claims_from_segment_wired,
    mine_preferences_from_segment_wired,
    mine_workflow_from_segment_wired,
)
from .next_input import NextInputWiringDeps, queue_next_input_wired
from .run_start import RunStartSeedsDeps, run_run_start_seeds
from .runtime_cfg import ParsedRuntimeFeatures, parse_runtime_features
from .segments import SegmentStateIO
from .state_warnings import StateWarningsFlusher

__all__ = [
    "BootstrappedAutopilotRun",
    "bootstrap_autopilot_run",
    "CheckPlanWiringDeps",
    "plan_checks_and_record_wired",
    "CheckpointWiringDeps",
    "run_checkpoint_pipeline_wired",
    "MindCaller",
    "ClaimMiningWiringDeps",
    "NodeMaterializeWiringDeps",
    "PreferenceMiningWiringDeps",
    "WorkflowMiningWiringDeps",
    "materialize_nodes_from_checkpoint_wired",
    "mine_claims_from_segment_wired",
    "mine_preferences_from_segment_wired",
    "mine_workflow_from_segment_wired",
    "NextInputWiringDeps",
    "queue_next_input_wired",
    "RunStartSeedsDeps",
    "run_run_start_seeds",
    "ParsedRuntimeFeatures",
    "parse_runtime_features",
    "SegmentStateIO",
    "StateWarningsFlusher",
]
