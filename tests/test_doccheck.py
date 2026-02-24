from __future__ import annotations

import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_DOCHECK_PATH = (Path(__file__).resolve().parents[1] / "scripts" / "doccheck.py").resolve()


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _git(cwd: Path, *args: str) -> str:
    p = _run(["git", *args], cwd=cwd)
    if p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return (p.stdout or "").strip()


def _write_text(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _init_min_repo(*, root: Path, last_updated: str) -> str:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test User")

    _write_text(
        root,
        "docs/mi-v1-spec.md",
        "\n".join(
            [
                "# MI V1 Spec",
                "",
                f"Last updated: {last_updated}",
                "",
                "Spec body.",
                "",
            ]
        ),
    )
    _write_text(root, "docs/mi-thought-db.md", "# Thought DB\n\nNotes.\n")
    _write_text(root, "docs/cli.md", "# CLI\n\nGuide.\n")
    _write_text(root, "docs/cli.zh-CN.md", "# CLI (ZH)\n\nGuide.\n")
    _write_text(root, "docs/internals.md", "# Internals\n\nNotes.\n")
    _write_text(root, "README.md", "# README\n\nHello.\n")
    _write_text(root, "README.zh-CN.md", "# README (ZH)\n\nHello.\n")
    _write_text(root, "references/doc-map.md", "# Doc Map\n\nKeep docs honest.\n")
    _write_text(root, "mi/runtime/runner.py", "# runner\n")
    _write_text(root, "mi/cli.py", "# cli\n")

    _git(root, "add", "-A")
    _git(root, "commit", "-m", "baseline", "-q")
    return _git(root, "rev-parse", "HEAD")


class TestDoccheck(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("git") is None:
            self.skipTest("git not available")

        if not _DOCHECK_PATH.is_file():
            self.skipTest(f"doccheck script missing: {_DOCHECK_PATH}")

    def test_code_change_requires_spec_update_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = _init_min_repo(root=root, last_updated="2000-01-01")

            _write_text(root, "mi/runtime/runner.py", "# runner\n\n# changed\n")
            _git(root, "add", "-A")
            _git(root, "commit", "-m", "change code", "-q")
            head = _git(root, "rev-parse", "HEAD")

            env = dict(os.environ)
            env["MI_DOCCHECK_STRICT"] = "1"
            p = _run([sys.executable, str(_DOCHECK_PATH), "--diff", f"{base}..{head}"], cwd=root, env=env)
            self.assertEqual(p.returncode, 1)
            self.assertIn("docs/mi-v1-spec.md not changed", p.stderr)

    def test_readme_bilingual_sync_warns_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = _init_min_repo(root=root, last_updated="2000-01-01")

            today = datetime.date.today().isoformat()
            _write_text(root, "mi/cli.py", "# cli\n\n# changed\n")
            _write_text(
                root,
                "docs/mi-v1-spec.md",
                "\n".join(
                    [
                        "# MI V1 Spec",
                        "",
                        f"Last updated: {today}",
                        "",
                        "Spec body changed.",
                        "",
                    ]
                ),
            )
            _write_text(root, "README.md", "# README\n\nUpdated.\n")

            _git(root, "add", "-A")
            _git(root, "commit", "-m", "cli + readme en only", "-q")
            head = _git(root, "rev-parse", "HEAD")

            env = dict(os.environ)
            env["MI_DOCCHECK_STRICT"] = "1"
            p = _run([sys.executable, str(_DOCHECK_PATH), "--diff", f"{base}..{head}"], cwd=root, env=env)
            self.assertEqual(p.returncode, 1)
            self.assertIn("README updated in only one language", p.stderr)

    def test_cli_change_requires_cli_doc_update_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = _init_min_repo(root=root, last_updated="2000-01-01")

            today = datetime.date.today().isoformat()
            _write_text(root, "mi/cli.py", "# cli\n\n# changed\n")
            _write_text(
                root,
                "docs/mi-v1-spec.md",
                "\n".join(
                    [
                        "# MI V1 Spec",
                        "",
                        f"Last updated: {today}",
                        "",
                        "Spec body changed.",
                        "",
                    ]
                ),
            )

            _git(root, "add", "-A")
            _git(root, "commit", "-m", "cli changed without cli doc", "-q")
            head = _git(root, "rev-parse", "HEAD")

            env = dict(os.environ)
            env["MI_DOCCHECK_STRICT"] = "1"
            p = _run([sys.executable, str(_DOCHECK_PATH), "--diff", f"{base}..{head}"], cwd=root, env=env)
            self.assertEqual(p.returncode, 1)
            self.assertIn("docs/cli.md not updated", p.stderr)

    def test_spec_last_updated_must_match_today_when_spec_changed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = _init_min_repo(root=root, last_updated="2000-01-01")

            # Change the spec but keep an old "Last updated" date to trigger the warning.
            _write_text(
                root,
                "docs/mi-v1-spec.md",
                "\n".join(
                    [
                        "# MI V1 Spec",
                        "",
                        "Last updated: 2000-01-01",
                        "",
                        "Spec changed.",
                        "",
                    ]
                ),
            )
            _git(root, "add", "-A")
            _git(root, "commit", "-m", "spec change without date bump", "-q")
            head = _git(root, "rev-parse", "HEAD")

            env = dict(os.environ)
            env["MI_DOCCHECK_STRICT"] = "1"
            p = _run([sys.executable, str(_DOCHECK_PATH), "--diff", f"{base}..{head}"], cwd=root, env=env)
            self.assertEqual(p.returncode, 1)
            self.assertIn("docs/mi-v1-spec.md changed but 'Last updated' is", p.stderr)

    def test_new_docs_require_doc_map_update_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = _init_min_repo(root=root, last_updated="2000-01-01")

            _write_text(root, "docs/new-guide.md", "# New Guide\n\nHello.\n")
            _git(root, "add", "-A")
            _git(root, "commit", "-m", "add docs", "-q")
            head = _git(root, "rev-parse", "HEAD")

            env = dict(os.environ)
            env["MI_DOCCHECK_STRICT"] = "1"
            p = _run([sys.executable, str(_DOCHECK_PATH), "--diff", f"{base}..{head}"], cwd=root, env=env)
            self.assertEqual(p.returncode, 1)
            self.assertIn("references/doc-map.md not updated", p.stderr)
