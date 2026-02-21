from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.memory.service import MemoryService
from mi.thoughtdb import ThoughtDbStore
from mi.thoughtdb.app_service import ThoughtDbApplicationService


@dataclass(frozen=True)
class _FakeMindResult:
    obj: dict
    transcript_path: Path


class _FakeMind:
    def __init__(self, obj: dict) -> None:
        self._obj = obj

    def call(self, *, schema_filename: str, prompt: str, tag: str) -> _FakeMindResult:
        _ = (schema_filename, prompt, tag)
        return _FakeMindResult(obj=self._obj, transcript_path=Path("fake_app_why.jsonl"))


class TestThoughtDbApplicationServiceWhy(unittest.TestCase):
    def test_event_why_helpers_and_materialized_edges(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)
            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)
            mem = MemoryService(home)

            event_id = "ev_app_why_0001"
            ev = {
                "kind": "evidence",
                "event_id": event_id,
                "batch_id": "b1",
                "ts": "2026-01-01T00:00:00Z",
                "thread_id": "",
                "facts": ["unify thought db access"],
                "actions": [],
                "results": ["ok"],
                "unknowns": [],
                "risk_signals": [],
            }
            pp.evidence_log_path.parent.mkdir(parents=True, exist_ok=True)
            pp.evidence_log_path.write_text(json.dumps(ev) + "\n", encoding="utf-8")

            cid = tdb.append_claim_create(
                claim_type="goal",
                text="Unify Thought DB call paths",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=[event_id],
                confidence=1.0,
                notes="",
            )

            app = ThoughtDbApplicationService(
                tdb=tdb,
                project_paths=pp,
                mem=mem,
                mind=_FakeMind(
                    {
                        "status": "ok",
                        "confidence": 0.9,
                        "chosen_claim_ids": [cid],
                        "explanation": "Matches event facts.",
                        "notes": "ok",
                    }
                ),
            )

            target_obj = app.find_evidence_event(evidence_log_path=pp.evidence_log_path, event_id=event_id)
            self.assertIsInstance(target_obj, dict)
            query = app.query_from_evidence_event(target_obj if isinstance(target_obj, dict) else {})
            cands = app.collect_why_candidates_for_target(
                target_obj=target_obj if isinstance(target_obj, dict) else {},
                query=query,
                top_k=12,
                as_of_ts="2026-01-01T00:00:00Z",
                target_event_id=event_id,
            )
            self.assertTrue(any(str(c.get("claim_id") or "") == cid for c in cands if isinstance(c, dict)))

            out = app.run_why_trace_for_target(
                target={"target_type": "evidence_event", "event_id": event_id},
                candidate_claims=cands,
                as_of_ts="2026-01-01T00:00:00Z",
                write_edges_from_event_id=event_id,
            )
            self.assertEqual(out.obj.get("chosen_claim_ids"), [cid])
            self.assertTrue(out.written_edge_ids)


if __name__ == "__main__":
    unittest.main()
