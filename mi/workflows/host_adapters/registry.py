from __future__ import annotations

from ..host_contracts import HostAdapter
from .openclaw import OpenClawSkillsAdapter


_HOST_ADAPTERS: dict[str, HostAdapter] = {
    "openclaw": OpenClawSkillsAdapter(),
}


def get_host_adapter(host: str) -> HostAdapter | None:
    return _HOST_ADAPTERS.get(str(host or "").strip().lower())

