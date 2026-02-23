from __future__ import annotations

import unittest

from mi.runtime.wiring.phase_inputs import normalize_phase_dicts


class TestPhaseInputs(unittest.TestCase):
    def test_normalize_phase_dicts(self) -> None:
        obj = normalize_phase_dicts(
            overlay={"a": 1},
            workflow_run={"b": 2},
            wf_cfg={"c": 3},
            pref_cfg={"d": 4},
            runtime_cfg={"e": 5},
        )
        self.assertEqual(obj.overlay, {"a": 1})
        self.assertEqual(obj.workflow_run, {"b": 2})
        self.assertEqual(obj.wf_cfg, {"c": 3})
        self.assertEqual(obj.pref_cfg, {"d": 4})
        self.assertEqual(obj.runtime_cfg, {"e": 5})

        obj2 = normalize_phase_dicts(
            overlay=None,
            workflow_run=[],
            wf_cfg="x",
            pref_cfg=0,
            runtime_cfg=object(),
        )
        self.assertEqual(obj2.overlay, {})
        self.assertEqual(obj2.workflow_run, {})
        self.assertEqual(obj2.wf_cfg, {})
        self.assertEqual(obj2.pref_cfg, {})
        self.assertEqual(obj2.runtime_cfg, {})


if __name__ == "__main__":
    unittest.main()

