from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class LearnSuggestedDeps:
    claim_signature_fn: Callable[..., str]
    existing_signature_map: Callable[[str], dict[str, str]]
    append_claim_create: Callable[..., str]
    evidence_append: Callable[[dict[str, Any]], Any]
    now_ts: Callable[[], str]
    new_suggestion_id: Callable[[], str]
    project_id: str
    thread_id: str


def apply_learn_suggested(
    *,
    learn_suggested: Any,
    batch_id: str,
    source: str,
    mind_transcript_ref: str,
    source_event_ids: list[str],
    runtime_cfg: dict[str, Any],
    deps: LearnSuggestedDeps,
) -> tuple[list[str], dict[str, Any] | None]:
    """Apply or record learn_suggested preference hints in strict Thought-DB mode."""

    vr = runtime_cfg.get("violation_response") if isinstance(runtime_cfg.get("violation_response"), dict) else {}
    auto_learn = bool(vr.get("auto_learn", True))

    norm: list[dict[str, Any]] = []
    if isinstance(learn_suggested, list):
        for ch in learn_suggested:
            if not isinstance(ch, dict):
                continue
            scope = str(ch.get("scope") or "").strip()
            text = str(ch.get("text") or "").strip()
            if scope not in ("global", "project") or not text:
                continue
            item: dict[str, Any] = {
                "scope": scope,
                "text": text,
                "rationale": str(ch.get("rationale") or "").strip(),
            }
            sev = str(ch.get("severity") or "").strip()
            if sev:
                item["severity"] = sev
            norm.append(item)

    if not norm:
        return [], None

    suggestion_id = deps.new_suggestion_id()
    applied_claim_ids: list[str] = []
    ev_ids = [str(x).strip() for x in (source_event_ids or []) if str(x).strip()][:8]

    sig_to_id = {
        "project": deps.existing_signature_map("project"),
        "global": deps.existing_signature_map("global"),
    }

    for item in norm:
        scope0 = str(item.get("scope") or "").strip()
        text = str(item.get("text") or "").strip()
        rationale = str(item.get("rationale") or "").strip()
        sev = str(item.get("severity") or "").strip()
        if scope0 not in ("global", "project") or not text:
            continue

        sc = "global" if scope0 == "global" else "project"
        pid = deps.project_id if sc == "project" else ""
        sig = deps.claim_signature_fn(claim_type="preference", scope=sc, project_id=pid, text=text)
        existing = sig_to_id.get(sc, {}).get(sig)
        if existing:
            applied_claim_ids.append(str(existing))
            continue

        if not auto_learn:
            continue

        tags: list[str] = ["mi:learned", "mi:pref", f"mi:source:{source}"]
        if sev:
            tags.append(f"severity:{sev}")

        base_r = rationale or source
        notes = f"{base_r} (source={source} suggestion={suggestion_id})"
        try:
            cid = deps.append_claim_create(
                claim_type="preference",
                text=text,
                scope=sc,
                visibility=("global" if sc == "global" else "project"),
                valid_from=None,
                valid_to=None,
                tags=tags,
                source_event_ids=ev_ids,
                confidence=1.0,
                notes=notes,
            )
        except Exception:
            continue

        sig_to_id.setdefault(sc, {})[sig] = cid
        applied_claim_ids.append(cid)

    rec = deps.evidence_append(
        {
            "kind": "learn_suggested",
            "id": suggestion_id,
            "batch_id": batch_id,
            "ts": deps.now_ts(),
            "thread_id": deps.thread_id,
            "source": source,
            "auto_learn": auto_learn,
            "mind_transcript_ref": str(mind_transcript_ref or ""),
            "learn_suggested": norm,
            "applied_claim_ids": applied_claim_ids,
            "source_event_ids": ev_ids,
        }
    )
    return applied_claim_ids, rec if isinstance(rec, dict) else None
