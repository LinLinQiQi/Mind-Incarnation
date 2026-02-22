from __future__ import annotations

import signal
from dataclasses import dataclass
from typing import Iterable

from ..runtime.risk import should_interrupt_text


@dataclass(frozen=True)
class InterruptConfig:
    mode: str  # off|on_high_risk|on_any_external
    signal_sequence: list[str]
    escalation_ms: list[int]


def signal_from_name(name: str) -> int | None:
    name = str(name or "").strip().upper()
    if not name:
        return None
    if not name.startswith("SIG"):
        name = "SIG" + name
    return getattr(signal, name, None)


def should_interrupt_command(mode: str, text: str) -> bool:
    return should_interrupt_text(mode, text)


def compute_escalation_delays_ms(escalation_ms: Iterable[int]) -> list[int]:
    # Always include the immediate first step.
    return [0] + [max(0, int(x)) for x in (escalation_ms or [])]


def escalation_delay_s_for_step(delays_ms: list[int], step_idx: int) -> float:
    ds = delays_ms if isinstance(delays_ms, list) else []
    if not ds:
        return 0.0
    idx = int(step_idx)
    if idx < 0:
        idx = 0
    if idx < len(ds):
        return ds[idx] / 1000.0
    return ds[-1] / 1000.0

