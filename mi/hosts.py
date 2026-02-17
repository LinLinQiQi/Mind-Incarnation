from __future__ import annotations

"""Back-compat wrapper for legacy imports.

Public API lives in `mi.workflows.hosts`.
"""

from .workflows.hosts import HostBinding, sync_host_binding, sync_hosts_from_overlay, parse_host_bindings

__all__ = ["HostBinding", "parse_host_bindings", "sync_host_binding", "sync_hosts_from_overlay"]

