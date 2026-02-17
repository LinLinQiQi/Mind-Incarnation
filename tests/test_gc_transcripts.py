import json
import tempfile
import unittest
from pathlib import Path

from mi.core.paths import ProjectPaths
from mi.core.storage import ensure_dir
from mi.runtime.gc import archive_project_transcripts
from mi.runtime.transcript import last_agent_message_from_transcript, resolve_transcript_path, tail_transcript_lines


def _write_transcript(path: Path, stdout_lines: list[str]) -> None:
    rows = []
    for i, s in enumerate(stdout_lines):
        rows.append({"ts": f"t{i}", "stream": "stdout", "line": s})
    path.write_text("\n".join([json.dumps(r, sort_keys=True) for r in rows]) + "\n", encoding="utf-8")


class TestGcTranscripts(unittest.TestCase):
    def test_archive_old_transcripts_and_preserve_readers(self) -> None:
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as project_root:
            pp = ProjectPaths(home_dir=Path(home), project_root=Path(project_root))
            hands_dir = pp.transcripts_dir / "hands"
            ensure_dir(hands_dir)

            t0 = hands_dir / "20240101T000000Z_b0.jsonl"
            t1 = hands_dir / "20240101T000001Z_b1.jsonl"
            t2 = hands_dir / "20240101T000002Z_b2.jsonl"

            _write_transcript(t0, ["first0", "last0"])
            _write_transcript(t1, ["first1", "last1"])
            _write_transcript(t2, ["first2", "last2"])

            res = archive_project_transcripts(transcripts_dir=pp.transcripts_dir, keep_hands=1, keep_mind=0, dry_run=False)
            hands = res.get("hands") if isinstance(res.get("hands"), dict) else {}
            self.assertEqual(hands.get("planned"), 2)

            # Older transcripts are replaced with stubs and point to a .gz archive.
            real0 = resolve_transcript_path(t0)
            real1 = resolve_transcript_path(t1)
            self.assertNotEqual(real0, t0)
            self.assertNotEqual(real1, t1)
            self.assertTrue(str(real0).endswith(".gz"))
            self.assertTrue(str(real1).endswith(".gz"))
            self.assertTrue(real0.exists())
            self.assertTrue(real1.exists())

            # Readers follow the stub and still see the original content.
            tail0 = tail_transcript_lines(t0, 2)
            self.assertTrue(tail0)
            self.assertIn("last0", tail0[-1])
            msg0 = last_agent_message_from_transcript(t0)
            self.assertEqual(msg0, "last0")

            # Most recent raw transcript remains unarchived.
            self.assertEqual(resolve_transcript_path(t2), t2)
            msg2 = last_agent_message_from_transcript(t2)
            self.assertEqual(msg2, "last2")


if __name__ == "__main__":
    unittest.main()
