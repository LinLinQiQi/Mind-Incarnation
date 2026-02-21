from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from typing import Any

from mi.cli_commands.values_set_flow import run_values_set_flow
from mi.core.paths import GlobalPaths, ProjectPaths
from mi.core.storage import iter_jsonl
from mi.thoughtdb import ThoughtDbStore


class _Obj:
    def __init__(self, obj: Any) -> None:
        self.obj = obj


class _FakeLlm:
    def __init__(self, *, compile_obj: Any = None, patch_obj: Any = None, compile_error: Exception | None = None, patch_error: Exception | None = None) -> None:
        self.compile_obj = compile_obj
        self.patch_obj = patch_obj
        self.compile_error = compile_error
        self.patch_error = patch_error
        self.calls: list[str] = []

    def call(self, *, schema_filename: str, prompt: str, tag: str) -> _Obj:
        self.calls.append(schema_filename)
        if schema_filename == "compile_values.json":
            if self.compile_error is not None:
                raise self.compile_error
            return _Obj(self.compile_obj if self.compile_obj is not None else {})
        if schema_filename == "values_claim_patch.json":
            if self.patch_error is not None:
                raise self.patch_error
            return _Obj(self.patch_obj if self.patch_obj is not None else {})
        raise AssertionError(f"unexpected schema: {schema_filename}")


def _make_global_tdb(home_dir: Path) -> ThoughtDbStore:
    pp = ProjectPaths(home_dir=home_dir, project_root=Path("."), _project_id="__global__")
    return ThoughtDbStore(home_dir=home_dir, project_paths=pp)


class TestValuesSetFlow(unittest.TestCase):
    def test_no_compile_writes_values_event_and_raw_claim(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            out = run_values_set_flow(
                home_dir=home,
                cfg={},
                make_global_tdb=lambda: _make_global_tdb(home),
                values_text="Prefer concise, behavior-preserving updates.",
                no_compile=True,
                no_values_claims=False,
                show=False,
                dry_run=False,
                notes="test-no-compile",
            )
            self.assertTrue(out.get("ok"))
            self.assertTrue(str(out.get("values_event_id") or "").strip())
            self.assertTrue(str(out.get("raw_claim_id") or "").strip())
            self.assertEqual(str((out.get("values_claims") or {}).get("skipped") or ""), "--no-compile")

            gp = GlobalPaths(home_dir=home)
            rows = [x for x in iter_jsonl(gp.global_evidence_log_path) if isinstance(x, dict)]
            self.assertTrue(any(str(x.get("kind") or "") == "values_set" for x in rows))

    def test_no_values_claims_skips_patch_after_compile(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            fake = _FakeLlm(
                compile_obj={
                    "values_summary": ["Prefer concise updates."],
                    "decision_procedure": {"summary": "Prefer concise outputs.", "mermaid": "graph TD;A-->B"},
                }
            )
            out = run_values_set_flow(
                home_dir=home,
                cfg={},
                make_global_tdb=lambda: _make_global_tdb(home),
                values_text="Prefer concise updates.",
                no_compile=False,
                no_values_claims=True,
                show=False,
                dry_run=False,
                notes="test-no-values-claims",
                mind_provider_factory=lambda _cfg, project_root, transcripts_dir: fake,
            )
            self.assertTrue(out.get("ok"))
            self.assertEqual(str((out.get("values_claims") or {}).get("skipped") or ""), "--no-values-claims")
            self.assertTrue(str(out.get("summary_node_id") or "").strip())
            self.assertEqual(fake.calls, ["compile_values.json"])

    def test_compile_and_patch_failures_are_non_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            fake = _FakeLlm(
                compile_error=RuntimeError("compile boom"),
                patch_error=RuntimeError("patch boom"),
            )
            err = io.StringIO()
            out = run_values_set_flow(
                home_dir=home,
                cfg={},
                make_global_tdb=lambda: _make_global_tdb(home),
                values_text="Prefer low user burden.",
                no_compile=False,
                no_values_claims=False,
                show=False,
                dry_run=False,
                notes="test-failure-fallback",
                mind_provider_factory=lambda _cfg, project_root, transcripts_dir: fake,
                stderr=err,
            )
            self.assertTrue(out.get("ok"))
            self.assertTrue(str(out.get("values_event_id") or "").strip())
            self.assertIn("patch boom", str((out.get("values_claims") or {}).get("error") or ""))
            self.assertIn("compile_values failed; falling back", err.getvalue())

    def test_dry_run_does_not_write_values_event(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            gp = GlobalPaths(home_dir=home)
            self.assertFalse(gp.global_evidence_log_path.exists())

            out = run_values_set_flow(
                home_dir=home,
                cfg={},
                make_global_tdb=lambda: _make_global_tdb(home),
                values_text="Prefer explicit rationale.",
                no_compile=True,
                no_values_claims=False,
                show=False,
                dry_run=True,
                notes="test-dry-run",
            )
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("dry_run"))
            self.assertFalse(gp.global_evidence_log_path.exists())


if __name__ == "__main__":
    unittest.main()
