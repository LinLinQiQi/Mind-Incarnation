from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, TextIO

from ..core.paths import GlobalPaths
from ..providers.provider_factory import make_mind_provider
from ..runtime.prompts import compile_values_prompt, values_claim_patch_prompt
from ..thoughtdb import ThoughtDbStore
from ..thoughtdb.operational_defaults import ensure_operational_defaults_claims_current
from ..thoughtdb.values import (
    apply_values_claim_patch,
    existing_values_claims,
    upsert_raw_values_claim,
    upsert_values_summary_node,
    write_values_set_event,
)


def run_values_set_flow(
    *,
    home_dir: Path,
    cfg: dict[str, Any],
    make_global_tdb: Callable[[], ThoughtDbStore],
    values_text: str,
    no_compile: bool,
    no_values_claims: bool,
    show: bool,
    dry_run: bool,
    notes: str,
    mind_provider_factory: Callable[..., Any] = make_mind_provider,
    compile_prompt_builder: Callable[..., str] = compile_values_prompt,
    values_claim_patch_prompt_builder: Callable[..., str] = values_claim_patch_prompt,
    stderr: TextIO = sys.stderr,
) -> dict[str, Any]:
    """Set canonical values in Thought DB (values event + raw claim; optional derived claims)."""

    values = str(values_text or "")
    if not values.strip():
        return {"ok": False, "error": "values text is empty"}

    llm = None
    compiled: dict[str, Any] | None = None
    compiled_from_model = False

    if not no_compile:
        # Run compile in an isolated directory to avoid accidental project context bleed.
        scratch = home_dir / "tmp" / "compile_values"
        scratch.mkdir(parents=True, exist_ok=True)
        gp = GlobalPaths(home_dir=home_dir)
        transcripts_dir = gp.global_dir / "transcripts"

        llm = mind_provider_factory(cfg, project_root=scratch, transcripts_dir=transcripts_dir)
        prompt = compile_prompt_builder(values_text=values)
        try:
            out = llm.call(schema_filename="compile_values.json", prompt=prompt, tag="compile_values").obj
            compiled = out if isinstance(out, dict) else None
            compiled_from_model = bool(compiled)
        except Exception as e:
            compiled = None
            print(f"compile_values failed; falling back. error={e}", file=stderr)

    compiled_values = compiled if isinstance(compiled, dict) else {}

    if show or dry_run:
        vs = compiled_values.get("values_summary") or []
        if isinstance(vs, list) and any(str(x).strip() for x in vs):
            print("values_summary:")
            for x in vs:
                xs = str(x).strip()
                if xs:
                    print(f"- {xs}")
        dp = compiled_values.get("decision_procedure") or {}
        if isinstance(dp, dict):
            summary = str(dp.get("summary") or "").strip()
            mermaid = str(dp.get("mermaid") or "").strip()
            if summary:
                print("\ndecision_procedure.summary:\n" + summary)
            if mermaid:
                print("\ndecision_procedure.mermaid:\n" + mermaid)

    if dry_run:
        return {"ok": True, "dry_run": True, "compiled": compiled_values, "compiled_from_model": compiled_from_model}

    # Record values changes into a global EvidenceLog so Claims can cite stable event_id provenance.
    values_ev = write_values_set_event(
        home_dir=home_dir,
        values_text=values,
        compiled_values=compiled_values,
        notes=str(notes or "").strip(),
    )
    values_event_id = str(values_ev.get("event_id") or "").strip()
    if not values_event_id:
        return {"ok": False, "error": "failed to record global values_set event_id"}

    tdb = make_global_tdb()
    raw_id = upsert_raw_values_claim(
        tdb=tdb,
        values_text=values,
        values_event_id=values_event_id,
        visibility="global",
        notes="values_text (raw)",
    )
    summary_id = ""
    if compiled_from_model:
        summary_id = upsert_values_summary_node(tdb=tdb, compiled_values=compiled_values, values_event_id=values_event_id)
    try:
        defaults_seed = ensure_operational_defaults_claims_current(
            home_dir=home_dir,
            tdb=tdb,
            desired_defaults=None,
            mode="seed_missing",
            event_notes=str(notes or "").strip() or "values_set",
            claim_notes_prefix="seed_on_values_set",
        )
    except Exception as e:
        defaults_seed = {"ok": False, "changed": False, "mode": "seed_missing", "event_id": "", "error": f"{type(e).__name__}: {e}"}

    # Derive values into canonical global preference/goal claims (best-effort).
    values_claims_applied: dict[str, Any] = {}
    values_claims_retracted: list[str] = []
    if no_compile:
        return {
            "ok": True,
            "values_event_id": values_event_id,
            "raw_claim_id": raw_id,
            "summary_node_id": summary_id,
            "defaults_seed": defaults_seed,
            "values_claims": {"skipped": "--no-compile"},
        }
    if no_values_claims:
        return {
            "ok": True,
            "values_event_id": values_event_id,
            "raw_claim_id": raw_id,
            "summary_node_id": summary_id,
            "defaults_seed": defaults_seed,
            "values_claims": {"skipped": "--no-values-claims"},
        }
    if llm is None:
        return {
            "ok": True,
            "values_event_id": values_event_id,
            "raw_claim_id": raw_id,
            "summary_node_id": summary_id,
            "defaults_seed": defaults_seed,
            "values_claims": {"skipped": "mind provider unavailable"},
        }

    try:
        existing = existing_values_claims(tdb=tdb, limit=120)
        retractable_ids = [
            str(c.get("claim_id") or "").strip()
            for c in existing
            if isinstance(c, dict) and str(c.get("claim_id") or "").strip()
        ]

        prompt2 = values_claim_patch_prompt_builder(
            values_text=values,
            compiled_values=compiled_values,
            existing_values_claims=existing,
            allowed_event_ids=[values_event_id],
            allowed_retract_claim_ids=retractable_ids,
            notes=str(notes or "").strip() or "values -> Thought DB claims",
        )
        patch_obj = llm.call(schema_filename="values_claim_patch.json", prompt=prompt2, tag="values_claim_patch").obj

        runtime_cfg = cfg.get("runtime") if isinstance(cfg.get("runtime"), dict) else {}
        tcfg = runtime_cfg.get("thought_db") if isinstance(runtime_cfg.get("thought_db"), dict) else {}
        try:
            min_conf = float(tcfg.get("min_confidence", 0.9) or 0.9)
        except Exception:
            min_conf = 0.9
        try:
            base_max = int(tcfg.get("max_claims_per_checkpoint", 6) or 6)
        except Exception:
            base_max = 6
        max_claims = max(8, min(20, base_max * 2))

        applied = apply_values_claim_patch(
            tdb=tdb,
            patch_obj=patch_obj if isinstance(patch_obj, dict) else {},
            values_event_id=values_event_id,
            min_confidence=min_conf,
            max_claims=max_claims,
        )
        if applied.ok:
            values_claims_applied = applied.applied if isinstance(applied.applied, dict) else {}
            values_claims_retracted = list(applied.retracted or [])
    except Exception as e:
        return {
            "ok": True,
            "values_event_id": values_event_id,
            "raw_claim_id": raw_id,
            "summary_node_id": summary_id,
            "defaults_seed": defaults_seed,
            "values_claims": {"error": f"{type(e).__name__}: {e}"},
        }

    return {
        "ok": True,
        "values_event_id": values_event_id,
        "raw_claim_id": raw_id,
        "summary_node_id": summary_id,
        "defaults_seed": defaults_seed,
        "values_claims": {"applied": values_claims_applied, "retracted": values_claims_retracted},
    }
