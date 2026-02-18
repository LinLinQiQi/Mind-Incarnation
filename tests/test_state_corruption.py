from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.core.storage import ensure_dir, read_json
from mi.project.overlay_store import load_project_overlay
from mi.workflows.store import load_workflow_candidates
from mi.workflows.preferences import load_preference_candidates
from mi.workflows.hosts import HostBinding, sync_host_binding


class TestStateCorruption(unittest.TestCase):
    def test_overlay_corrupt_quarantined_and_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            ensure_dir(pp.project_dir)

            # Simulate a partial write / corruption.
            pp.overlay_path.write_text("{", encoding="utf-8")

            warnings: list[dict] = []
            overlay = load_project_overlay(home_dir=Path(home), project_root=Path(project_root), warnings=warnings)
            self.assertIsInstance(overlay, dict)
            self.assertTrue(str(overlay.get("project_id") or "").strip())
            self.assertTrue(warnings)

            # A fresh overlay.json should exist and be valid JSON.
            obj = read_json(pp.overlay_path, default=None)
            self.assertIsInstance(obj, dict)

            # The corrupt file is quarantined.
            q = list(pp.overlay_path.parent.glob("overlay.json.corrupt.*"))
            self.assertTrue(q)

    def test_candidates_corrupt_quarantined_and_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            ensure_dir(pp.project_dir)

            pp.workflow_candidates_path.write_text("{", encoding="utf-8")
            pp.preference_candidates_path.write_text("{", encoding="utf-8")

            warnings: list[dict] = []
            wf = load_workflow_candidates(pp, warnings=warnings)
            pref = load_preference_candidates(pp, warnings=warnings)

            self.assertIsInstance(wf, dict)
            self.assertIsInstance(wf.get("by_signature"), dict)
            self.assertIsInstance(pref, dict)
            self.assertIsInstance(pref.get("by_signature"), dict)
            self.assertTrue(warnings)

            q1 = list(pp.workflow_candidates_path.parent.glob("workflow_candidates.json.corrupt.*"))
            q2 = list(pp.preference_candidates_path.parent.glob("preference_candidates.json.corrupt.*"))
            self.assertTrue(q1)
            self.assertTrue(q2)

    def test_host_manifest_corrupt_quarantined_and_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            ws = Path(workspace)
            ensure_dir(ws)

            binding = HostBinding(
                host="openclaw",
                workspace_root=ws,
                enabled=True,
                generated_rel_dir=".mi/generated/openclaw",
                register_symlink_dirs=[],
            )

            gen = binding.generated_root
            ensure_dir(gen)
            manifest = gen / "manifest.json"
            manifest.write_text("{", encoding="utf-8")

            warnings: list[dict] = []
            _res = sync_host_binding(binding=binding, project_id="p_test", workflows=[], warnings=warnings)
            self.assertTrue(warnings)

            obj = read_json(manifest, default=None)
            self.assertIsInstance(obj, dict)

            q = list(gen.glob("manifest.json.corrupt.*"))
            self.assertTrue(q)


if __name__ == "__main__":
    unittest.main()
