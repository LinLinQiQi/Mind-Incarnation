import json
import tempfile
import unittest
from pathlib import Path

from mi.mindspec import MindSpecStore


class TestLearnedScoping(unittest.TestCase):
    def test_project_disable_can_mask_global_entry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            store = MindSpecStore(home_dir=str(home))
            project_root = home / "proj"
            project_root.mkdir()

            store.write_base_values(values_text="v")
            global_id = store.append_learned(project_root=project_root, scope="global", text="GLOBAL", rationale="r")

            # By default, learned text includes GLOBAL.
            self.assertIn("GLOBAL", store.load_learned_text(project_root))

            # Disable it for this project only.
            store.disable_learned(project_root=project_root, scope="project", target_id=global_id, rationale="nope")
            self.assertNotIn("GLOBAL", store.load_learned_text(project_root))

            # But it still exists in global learned.jsonl.
            global_path = home / "mindspec" / "learned.jsonl"
            lines = global_path.read_text(encoding="utf-8").strip().splitlines()
            objs = [json.loads(x) for x in lines]
            self.assertTrue(any(o.get("id") == global_id for o in objs))


if __name__ == "__main__":
    unittest.main()

