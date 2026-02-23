from __future__ import annotations

import unittest

from mi.thoughtdb.model import ThoughtDbView
from mi.thoughtdb import predicates as P


def _mk_view(*, scope: str = "project") -> ThoughtDbView:
    return ThoughtDbView(
        scope=scope,
        project_id=("p1" if scope == "project" else ""),
        claims_by_id={},
        nodes_by_id={},
        edges=[],
        redirects_same_as={},
        superseded_ids=set(),
        retracted_ids=set(),
        retracted_node_ids=set(),
        claims_by_tag={},
        nodes_by_tag={},
        edges_by_from={},
        edges_by_to={},
        claim_ids_by_asserted_ts_desc=[],
        node_ids_by_asserted_ts_desc=[],
    )


class TestThoughtDbRetrievalHelpers(unittest.TestCase):
    def test_claim_active_and_valid_filters_redirects(self) -> None:
        v = _mk_view()
        v = ThoughtDbView(
            **{
                **v.__dict__,
                "redirects_same_as": {"cl_dup": "cl_canon"},
                "claims_by_id": {"cl_dup": {"claim_id": "cl_dup", "valid_from": None, "valid_to": None}},
            }
        )
        self.assertFalse(P.claim_active_and_valid(v, "cl_dup", as_of_ts="2026-02-23T00:00:00Z"))

    def test_claim_active_and_valid_filters_superseded_and_retracted(self) -> None:
        v = _mk_view()
        v = ThoughtDbView(
            **{
                **v.__dict__,
                "claims_by_id": {"cl_old": {"claim_id": "cl_old"}, "cl_bad": {"claim_id": "cl_bad"}},
                "superseded_ids": {"cl_old"},
                "retracted_ids": {"cl_bad"},
            }
        )
        self.assertFalse(P.claim_active_and_valid(v, "cl_old", as_of_ts="2026-02-23T00:00:00Z"))
        self.assertFalse(P.claim_active_and_valid(v, "cl_bad", as_of_ts="2026-02-23T00:00:00Z"))

    def test_claim_active_and_valid_honors_valid_window(self) -> None:
        v = _mk_view()
        v = ThoughtDbView(
            **{
                **v.__dict__,
                "claims_by_id": {
                    "cl_window": {"claim_id": "cl_window", "valid_from": "2026-02-20T00:00:00Z", "valid_to": "2026-02-24T00:00:00Z"}
                },
            }
        )
        self.assertTrue(P.claim_active_and_valid(v, "cl_window", as_of_ts="2026-02-23T00:00:00Z"))
        self.assertFalse(P.claim_active_and_valid(v, "cl_window", as_of_ts="2026-02-19T00:00:00Z"))
        self.assertFalse(P.claim_active_and_valid(v, "cl_window", as_of_ts="2026-02-24T00:00:00Z"))

    def test_node_active_filters_redirects_and_retractions(self) -> None:
        v = _mk_view()
        v = ThoughtDbView(
            **{
                **v.__dict__,
                "redirects_same_as": {"nd_dup": "nd_canon"},
                "nodes_by_id": {"nd_dup": {"node_id": "nd_dup"}, "nd_bad": {"node_id": "nd_bad"}},
                "retracted_node_ids": {"nd_bad"},
            }
        )
        self.assertFalse(P.node_active(v, "nd_dup"))
        self.assertFalse(P.node_active(v, "nd_bad"))


if __name__ == "__main__":
    unittest.main()
