from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .global_ledger import append_global_event, iter_global_events
from .pins import ASK_WHEN_UNCERTAIN_TAG, REFACTOR_INTENT_TAG
from ..core.storage import now_rfc3339
from .store import ThoughtDbStore, ThoughtDbView, claim_signature


DEFAULTS_EVENT_KIND = "mi_defaults_set"

_ASK_PREFIX = "MI setting: ask_when_uncertain ="
_REF_PREFIX = "MI setting: refactor_intent ="


def _norm(text: str) -> str:
    return " ".join((text or "").strip().split())


def _setting_value_from_text(*, prefix: str, text: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    for line in t.splitlines():
        s = line.strip()
        if s.startswith(prefix):
            return _norm(s[len(prefix) :])
    return ""


def ask_when_uncertain_claim_text(value: bool) -> str:
    return f"{_ASK_PREFIX} {'ask' if bool(value) else 'proceed'}"


def refactor_intent_claim_text(value: str) -> str:
    v = str(value or "").strip()
    if v not in ("behavior_preserving", "behavior_changing"):
        v = "behavior_preserving"
    return f"{_REF_PREFIX} {v}"


def _parse_ask_when_uncertain(text: str) -> bool | None:
    v = _setting_value_from_text(prefix=_ASK_PREFIX, text=text).lower()
    if v in ("ask", "true", "yes", "1"):
        return True
    if v in ("proceed", "false", "no", "0"):
        return False
    return None


def _parse_refactor_intent(text: str) -> str:
    v = _setting_value_from_text(prefix=_REF_PREFIX, text=text).strip()
    return v if v in ("behavior_preserving", "behavior_changing") else ""


def _tagset(obj: dict[str, Any]) -> set[str]:
    tags = obj.get("tags") if isinstance(obj.get("tags"), list) else []
    return {str(x).strip() for x in tags if str(x).strip()}


def _find_tagged_claim(*, view: ThoughtDbView, as_of_ts: str, tag: str) -> dict[str, Any] | None:
    """Find the newest active canonical preference/goal claim with a given tag (best-effort)."""

    best: dict[str, Any] | None = None
    best_ts = ""
    want = str(tag or "").strip()
    if not want:
        return None
    for c in view.iter_claims(include_inactive=False, include_aliases=False, as_of_ts=as_of_ts):
        if not isinstance(c, dict):
            continue
        ct = str(c.get("claim_type") or "").strip()
        if ct not in ("preference", "goal"):
            continue
        if want not in _tagset(c):
            continue
        ts = str(c.get("asserted_ts") or "").strip()
        if ts >= best_ts:
            best = c
            best_ts = ts
    return best


@dataclass(frozen=True)
class OperationalDefaults:
    refactor_intent: str
    ask_when_uncertain: bool
    refactor_intent_source: dict[str, str]
    ask_when_uncertain_source: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "refactor_intent": self.refactor_intent,
            "ask_when_uncertain": self.ask_when_uncertain,
            "refactor_intent_source": self.refactor_intent_source,
            "ask_when_uncertain_source": self.ask_when_uncertain_source,
        }


def resolve_operational_defaults(
    *,
    tdb: ThoughtDbStore,
    mindspec_base: dict[str, Any] | None,
    as_of_ts: str,
) -> OperationalDefaults:
    """Resolve operational defaults from canonical Thought DB claims (project overrides global)."""

    base = mindspec_base if isinstance(mindspec_base, dict) else {}
    defaults = base.get("defaults") if isinstance(base.get("defaults"), dict) else {}

    fb_ref = str(defaults.get("refactor_intent") or "behavior_preserving").strip()
    if fb_ref not in ("behavior_preserving", "behavior_changing"):
        fb_ref = "behavior_preserving"
    fb_ask = bool(defaults.get("ask_when_uncertain", True))

    v_proj = tdb.load_view(scope="project")
    v_glob = tdb.load_view(scope="global")

    ask_src: dict[str, str] = {"scope": "", "claim_id": ""}
    ref_src: dict[str, str] = {"scope": "", "claim_id": ""}

    ask_val: bool = fb_ask
    ref_val: str = fb_ref

    for view, scope in ((v_proj, "project"), (v_glob, "global")):
        c = _find_tagged_claim(view=view, as_of_ts=as_of_ts, tag=ASK_WHEN_UNCERTAIN_TAG)
        if isinstance(c, dict):
            parsed = _parse_ask_when_uncertain(str(c.get("text") or ""))
            if parsed is not None:
                ask_val = bool(parsed)
                ask_src = {"scope": scope, "claim_id": str(c.get("claim_id") or "").strip()}
                break

    for view, scope in ((v_proj, "project"), (v_glob, "global")):
        c = _find_tagged_claim(view=view, as_of_ts=as_of_ts, tag=REFACTOR_INTENT_TAG)
        if isinstance(c, dict):
            parsed = _parse_refactor_intent(str(c.get("text") or ""))
            if parsed:
                ref_val = parsed
                ref_src = {"scope": scope, "claim_id": str(c.get("claim_id") or "").strip()}
                break

    return OperationalDefaults(
        refactor_intent=ref_val,
        ask_when_uncertain=ask_val,
        refactor_intent_source=ref_src,
        ask_when_uncertain_source=ask_src,
    )


def _last_defaults_event(*, home_dir: Path) -> tuple[str, dict[str, Any]]:
    last_id = ""
    last_payload: dict[str, Any] = {}
    for ev in iter_global_events(home_dir=home_dir):
        if not isinstance(ev, dict):
            continue
        if str(ev.get("kind") or "").strip() != DEFAULTS_EVENT_KIND:
            continue
        last_id = str(ev.get("event_id") or "").strip()
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        last_payload = payload if isinstance(payload, dict) else {}
    return last_id, last_payload


def ensure_operational_defaults_claims_current(
    *,
    home_dir: Path,
    tdb: ThoughtDbStore,
    mindspec_base: dict[str, Any] | None,
    mode: str,
    event_notes: str = "",
    claim_notes_prefix: str = "auto_migrate",
) -> dict[str, Any]:
    """Ensure operational defaults are canonical preference Claims (append-only; best-effort).

    mode:
    - "seed_missing": create claims only when no tagged claim exists yet
    - "sync": keep claims in sync with MindSpec base.defaults (supersede on change)
    """

    base = mindspec_base if isinstance(mindspec_base, dict) else {}
    defaults = base.get("defaults") if isinstance(base.get("defaults"), dict) else {}

    desired_ref = str(defaults.get("refactor_intent") or "behavior_preserving").strip()
    if desired_ref not in ("behavior_preserving", "behavior_changing"):
        desired_ref = "behavior_preserving"
    desired_ask = bool(defaults.get("ask_when_uncertain", True))

    desired = {
        "refactor_intent": desired_ref,
        "ask_when_uncertain": desired_ask,
    }

    # Determine whether we need to write anything (GLOBAL defaults only).
    as_of = now_rfc3339()
    v_glob = tdb.load_view(scope="global")

    glob_ask = _find_tagged_claim(view=v_glob, as_of_ts=as_of, tag=ASK_WHEN_UNCERTAIN_TAG)
    glob_ask_id = str(glob_ask.get("claim_id") or "").strip() if isinstance(glob_ask, dict) else ""
    glob_ask_val = _parse_ask_when_uncertain(str(glob_ask.get("text") or "")) if isinstance(glob_ask, dict) else None

    glob_ref = _find_tagged_claim(view=v_glob, as_of_ts=as_of, tag=REFACTOR_INTENT_TAG)
    glob_ref_id = str(glob_ref.get("claim_id") or "").strip() if isinstance(glob_ref, dict) else ""
    glob_ref_val = _parse_refactor_intent(str(glob_ref.get("text") or "")) if isinstance(glob_ref, dict) else ""

    if str(mode or "").strip() == "seed_missing":
        need = (not glob_ask_id) or (not glob_ref_id)
    else:
        # sync mode: write when global values differ from desired, or claims are missing/unparseable.
        need = (
            (not glob_ask_id)
            or (glob_ask_val is None)
            or (bool(glob_ask_val) != bool(desired_ask))
            or (not glob_ref_id)
            or (not glob_ref_val)
            or (glob_ref_val != desired_ref)
        )

    if not need:
        return {"ok": True, "changed": False, "mode": mode, "event_id": "", "desired": desired, "applied": {}}

    # Reuse last matching defaults_set event_id if possible; otherwise append a new one.
    last_id, last_payload = _last_defaults_event(home_dir=home_dir)
    last_defaults = last_payload.get("defaults") if isinstance(last_payload.get("defaults"), dict) else {}
    event_id = last_id if (last_id and last_defaults == desired) else ""
    if not event_id:
        note = str(event_notes or "").strip() or "auto_migrate"
        rec = append_global_event(home_dir=home_dir, kind=DEFAULTS_EVENT_KIND, payload={"defaults": desired, "notes": note})
        event_id = str(rec.get("event_id") or "").strip()

    if not event_id:
        return {"ok": False, "changed": False, "mode": mode, "event_id": "", "desired": desired, "error": "failed to write defaults_set event"}

    sig_map = tdb.existing_signature_map(scope="global")

    written: list[str] = []
    superseded: list[dict[str, str]] = []

    def upsert(*, tag: str, text: str, existing_claim_id: str) -> str:
        # Dedupe by signature first (idempotent across runs).
        sig = claim_signature(claim_type="preference", scope="global", project_id="", text=text)
        if sig in sig_map:
            cid0 = str(sig_map[sig])
            if existing_claim_id and existing_claim_id != cid0:
                try:
                    tdb.append_edge(
                        edge_type="supersedes",
                        from_id=existing_claim_id,
                        to_id=cid0,
                        scope="global",
                        visibility="global",
                        source_event_ids=[event_id],
                        notes="operational defaults dedupe",
                    )
                    superseded.append({"from": existing_claim_id, "to": cid0})
                except Exception:
                    pass
            return cid0

        tags = [tag, "mi:setting", "mi:defaults"]
        notes = str(claim_notes_prefix or "").strip() or "auto_migrate"
        cid = tdb.append_claim_create(
            claim_type="preference",
            text=text,
            scope="global",
            visibility="global",
            valid_from=None,
            valid_to=None,
            tags=tags,
            source_event_ids=[event_id],
            confidence=1.0,
            notes=f"{notes} {DEFAULTS_EVENT_KIND} {event_id}",
        )
        written.append(cid)
        sig_map[sig] = cid

        if existing_claim_id:
            try:
                tdb.append_edge(
                    edge_type="supersedes",
                    from_id=existing_claim_id,
                    to_id=cid,
                    scope="global",
                    visibility="global",
                    source_event_ids=[event_id],
                    notes="operational defaults update",
                )
                superseded.append({"from": existing_claim_id, "to": cid})
            except Exception:
                pass
        return cid

    # Upsert global claims; keep project overrides intact by only writing to global.
    existing_ask_id = glob_ask_id
    existing_ref_id = glob_ref_id

    ask_claim = ask_when_uncertain_claim_text(desired_ask)
    ref_claim = refactor_intent_claim_text(desired_ref)

    upsert(tag=ASK_WHEN_UNCERTAIN_TAG, text=ask_claim, existing_claim_id=existing_ask_id)
    upsert(tag=REFACTOR_INTENT_TAG, text=ref_claim, existing_claim_id=existing_ref_id)

    return {
        "ok": True,
        "changed": True,
        "mode": mode,
        "event_id": event_id,
        "desired": desired,
        "applied": {"written_claim_ids": written, "superseded": superseded},
    }
