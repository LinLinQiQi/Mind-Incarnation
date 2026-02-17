from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.memory.service import MemoryService
from mi.thoughtdb import ThoughtDbStore
from mi.thoughtdb.why import collect_candidate_claims, query_from_evidence_event, run_why_trace


@dataclass(frozen=True)
class _FakeMindResult:
    obj: dict
    transcript_path: Path


class _FakeMind:
    def __init__(self, obj: dict) -> None:
        self._obj = obj

    def call(self, *, schema_filename: str, prompt: str, tag: str) -> _FakeMindResult:
        _ = (schema_filename, prompt, tag)
        return _FakeMindResult(obj=self._obj, transcript_path=Path("fake_why_trace.jsonl"))


class TestWhyTrace(unittest.TestCase):
    def test_why_trace_writes_event_depends_on_edges(self) -> None:
        with tempfile.TemporaryDirectory() as td_home, tempfile.TemporaryDirectory() as td_proj:
            home = Path(td_home)
            project_root = Path(td_proj)

            pp = ProjectPaths(home_dir=home, project_root=project_root)
            tdb = ThoughtDbStore(home_dir=home, project_paths=pp)
            mem = MemoryService(home)

            event_id = "ev_test_why_000001"
            ev = {
                "kind": "evidence",
                "event_id": event_id,
                "batch_id": "b1",
                "ts": "2026-01-01T00:00:00Z",
                "thread_id": "",
                "facts": ["ship v1", "minimal user burden"],
                "actions": [],
                "results": ["ok"],
                "unknowns": [],
                "risk_signals": [],
            }
            pp.evidence_log_path.parent.mkdir(parents=True, exist_ok=True)
            pp.evidence_log_path.write_text(json.dumps(ev) + "\n", encoding="utf-8")

            cid = tdb.append_claim_create(
                claim_type="goal",
                text="Ship v1 with minimal user burden",
                scope="project",
                visibility="project",
                valid_from=None,
                valid_to=None,
                tags=[],
                source_event_ids=[event_id],
                confidence=1.0,
                notes="",
            )

            query = query_from_evidence_event(ev)
            candidates = collect_candidate_claims(tdb=tdb, mem=mem, project_paths=pp, query=query, top_k=12, target_event_id=event_id)
            self.assertTrue(any(str(c.get("claim_id") or "") == cid for c in candidates if isinstance(c, dict)))

            fake_mind = _FakeMind(
                {
                    "status": "ok",
                    "confidence": 0.9,
                    "chosen_claim_ids": [cid],
                    "explanation": "Because the goal claim matches the evidence facts.",
                    "notes": "ok",
                }
            )
            out = run_why_trace(
                mind=fake_mind,
                tdb=tdb,
                mem=mem,
                project_paths=pp,
                target={"target_type": "evidence_event", "event_id": event_id},
                candidate_claims=candidates,
                as_of_ts="2026-01-01T00:00:00Z",
                write_edges_from_event_id=event_id,
            )
            self.assertEqual(out.obj.get("chosen_claim_ids"), [cid])
            self.assertTrue(out.written_edge_ids)

            v = tdb.load_view(scope="project")
            self.assertTrue(any(e.get("edge_type") == "depends_on" and e.get("from_id") == event_id and e.get("to_id") == cid for e in v.edges if isinstance(e, dict)))


if __name__ == "__main__":
    unittest.main()
