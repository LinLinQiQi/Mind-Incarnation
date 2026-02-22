"""Runtime wiring helpers (internal).

This package exists to keep mi/runtime/runner.py small and behavior-stable by
centralizing bootstrap/config parsing code in testable modules.
"""

from .bootstrap import BootstrappedAutopilotRun, bootstrap_autopilot_run
from .runtime_cfg import ParsedRuntimeFeatures, parse_runtime_features
from .segments import SegmentStateIO

__all__ = [
    "BootstrappedAutopilotRun",
    "bootstrap_autopilot_run",
    "ParsedRuntimeFeatures",
    "parse_runtime_features",
    "SegmentStateIO",
]
