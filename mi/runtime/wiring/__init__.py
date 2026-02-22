"""Runtime wiring helpers (internal).

This package exists to keep mi/runtime/runner.py small and behavior-stable by
centralizing bootstrap/config parsing code in testable modules.
"""

from .bootstrap import BootstrappedAutopilotRun, bootstrap_autopilot_run
from .auto_answer import (
    AutoAnswerQueryWiringDeps,
    query_auto_answer_to_hands_wired,
)
from .ask_user import (
    AskUserAutoAnswerAttemptWiringDeps,
    AskUserRedecideWithInputWiringDeps,
    DecideAskUserWiringDeps,
    ask_user_auto_answer_attempt_wired,
    ask_user_redecide_with_input_wired,
    handle_decide_next_ask_user_wired,
)
from .check_plan import CheckPlanWiringDeps, plan_checks_and_record_wired
from .checkpoints import CheckpointWiringDeps, run_checkpoint_pipeline_wired
from .decide_next import (
    DecideNextQueryWiringDeps,
    DecideRecordEffectsWiringDeps,
    query_decide_next_wired,
    record_decide_next_effects_wired,
)
from .loop_break_checks import (
    LoopBreakChecksWiringDeps,
    loop_break_get_checks_input_wired,
)
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
from .interaction_record import (
    InteractionRecordWiringDeps,
    append_auto_answer_record_wired,
    append_user_input_record_wired,
)
from .next_input import NextInputWiringDeps, queue_next_input_wired
from .run_start import RunStartSeedsDeps, run_run_start_seeds
from .runtime_cfg import ParsedRuntimeFeatures, parse_runtime_features
from .segments import SegmentStateIO
from .state_warnings import StateWarningsFlusher
from .risk_predecide import (
    RiskEventRecordWiringDeps,
    RiskJudgeWiringDeps,
    append_risk_event_wired,
    query_risk_judge_wired,
)
from .workflow_progress import (
    WorkflowProgressWiringDeps,
    apply_workflow_progress_wired,
)
from .predecide_user import (
    PredecideUserWiringDeps,
    handle_auto_answer_needs_user_wired,
    prompt_user_then_queue_wired,
    retry_auto_answer_after_recall_wired,
    try_queue_answer_with_checks_wired,
)
from .testless_strategy import (
    TestlessResolutionWiringDeps,
    TestlessStrategyWiringDeps,
    apply_set_testless_strategy_overlay_update_wired,
    mk_testless_strategy_flow_deps_wired,
    resolve_tls_for_checks_wired,
)

__all__ = [
    "BootstrappedAutopilotRun",
    "bootstrap_autopilot_run",
    "AutoAnswerQueryWiringDeps",
    "query_auto_answer_to_hands_wired",
    "AskUserAutoAnswerAttemptWiringDeps",
    "ask_user_auto_answer_attempt_wired",
    "AskUserRedecideWithInputWiringDeps",
    "ask_user_redecide_with_input_wired",
    "DecideAskUserWiringDeps",
    "handle_decide_next_ask_user_wired",
    "CheckPlanWiringDeps",
    "plan_checks_and_record_wired",
    "CheckpointWiringDeps",
    "run_checkpoint_pipeline_wired",
    "DecideNextQueryWiringDeps",
    "query_decide_next_wired",
    "DecideRecordEffectsWiringDeps",
    "record_decide_next_effects_wired",
    "PredecideUserWiringDeps",
    "retry_auto_answer_after_recall_wired",
    "try_queue_answer_with_checks_wired",
    "prompt_user_then_queue_wired",
    "handle_auto_answer_needs_user_wired",
    "LoopBreakChecksWiringDeps",
    "loop_break_get_checks_input_wired",
    "MindCaller",
    "TestlessResolutionWiringDeps",
    "TestlessStrategyWiringDeps",
    "apply_set_testless_strategy_overlay_update_wired",
    "mk_testless_strategy_flow_deps_wired",
    "resolve_tls_for_checks_wired",
    "ClaimMiningWiringDeps",
    "NodeMaterializeWiringDeps",
    "PreferenceMiningWiringDeps",
    "WorkflowMiningWiringDeps",
    "materialize_nodes_from_checkpoint_wired",
    "mine_claims_from_segment_wired",
    "mine_preferences_from_segment_wired",
    "mine_workflow_from_segment_wired",
    "InteractionRecordWiringDeps",
    "append_user_input_record_wired",
    "append_auto_answer_record_wired",
    "NextInputWiringDeps",
    "queue_next_input_wired",
    "RunStartSeedsDeps",
    "run_run_start_seeds",
    "ParsedRuntimeFeatures",
    "parse_runtime_features",
    "SegmentStateIO",
    "StateWarningsFlusher",
    "RiskJudgeWiringDeps",
    "query_risk_judge_wired",
    "RiskEventRecordWiringDeps",
    "append_risk_event_wired",
    "WorkflowProgressWiringDeps",
    "apply_workflow_progress_wired",
]
