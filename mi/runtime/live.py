from __future__ import annotations

from typing import Any


def _truncate(text: str, limit: int) -> str:
    s = str(text or "")
    if limit <= 0:
        return ""
    if len(s) <= limit:
        return s
    return s[: max(0, limit - 3)] + "..."


def _as_dict(x: Any) -> dict[str, Any]:
    return x if isinstance(x, dict) else {}


def render_codex_event(
    ev: dict[str, Any],
    *,
    max_output_chars: int = 4000,
    max_message_chars: int = 8000,
) -> list[str]:
    """Render a Codex `--json` event into user-facing lines.

    Keep this intentionally minimal and resilient: event schemas may evolve,
    and we only want durable, human-readable signals for live CLI display.
    """

    et = str(ev.get("type") or "").strip()

    if et == "thread.started":
        tid = str(ev.get("thread_id") or "").strip()
        return [f"thread.started thread_id={tid}" if tid else "thread.started"]

    if et == "item.started":
        item = _as_dict(ev.get("item"))
        itype = str(item.get("type") or "").strip()
        if itype == "command_execution":
            cmd = str(item.get("command") or "").strip()
            if cmd:
                return [f"$ {_truncate(cmd, 400)}"]
        return []

    if et == "item.completed":
        item = _as_dict(ev.get("item"))
        itype = str(item.get("type") or "").strip()

        if itype == "command_execution":
            cmd = str(item.get("command") or "").strip()
            exit_code = item.get("exit_code")
            out = str(item.get("aggregated_output") or "")

            head = "$ " + _truncate(cmd, 400) if cmd else "command_execution"
            tail: list[str] = []
            if exit_code is not None:
                try:
                    code_s = str(int(exit_code))
                except Exception:
                    code_s = str(exit_code)
                tail.append(f"(exit_code={code_s}) {head}")
            else:
                tail.append(head)

            out_s = _truncate(out, max_output_chars).rstrip("\n")
            if out_s.strip():
                tail.extend(out_s.splitlines())
            return tail

        if itype == "agent_message":
            text = str(item.get("text") or "")
            text_s = _truncate(text, max_message_chars).rstrip("\n")
            if not text_s.strip():
                return []
            return text_s.splitlines()

        return []

    return []

