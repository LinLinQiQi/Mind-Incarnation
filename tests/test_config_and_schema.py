import json
import os
import tempfile
import unittest
from pathlib import Path

from mi.config import (
    default_config,
    init_config,
    load_config,
    config_for_display,
    validate_config,
    list_config_templates,
    get_config_template,
)
from mi.schema_validate import validate_json_schema


class TestConfigAndSchema(unittest.TestCase):
    def test_default_config_is_minimal(self) -> None:
        cfg = default_config()
        self.assertEqual(cfg["version"], "v1")
        self.assertIn("mind", cfg)
        self.assertIn("hands", cfg)

    def test_init_and_load_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = init_config(home, force=False)
            self.assertTrue(path.exists())

            cfg = load_config(home)
            self.assertEqual(cfg["version"], "v1")

            disp = config_for_display(cfg)
            # api_key should be redacted if present; default is empty so should stay empty.
            self.assertIn("mind", disp)

    def test_deep_merge_preserves_nested_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = init_config(home, force=True)
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["mind"]["provider"] = "openai_compatible"
            raw["mind"]["openai_compatible"]["model"] = "x"
            path.write_text(json.dumps(raw), encoding="utf-8")

            cfg = load_config(home)
            self.assertEqual(cfg["mind"]["provider"], "openai_compatible")
            self.assertIn("anthropic", cfg["mind"])  # still present from defaults

    def test_schema_validation_basic(self) -> None:
        schema_path = Path(__file__).resolve().parent.parent / "mi" / "schemas" / "decide_next.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        ok = {
            "next_action": "stop",
            "status": "done",
            "confidence": 0.9,
            "next_codex_input": "",
            "ask_user_question": "",
            "learned_changes": [],
            "update_project_overlay": {"set_testless_strategy": None},
            "notes": "done",
        }
        self.assertEqual(validate_json_schema(ok, schema), [])

        bad = dict(ok)
        bad.pop("notes")
        errs = validate_json_schema(bad, schema)
        self.assertTrue(errs)

    def test_validate_config_reports_missing_commands_when_path_empty(self) -> None:
        cfg = default_config()
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ""
        try:
            report = validate_config(cfg)
        finally:
            os.environ["PATH"] = old_path

        self.assertIn("ok", report)
        self.assertIn("errors", report)
        self.assertIsInstance(report.get("errors"), list)
        self.assertFalse(bool(report.get("ok")))
        self.assertTrue(any("command not found" in str(e) for e in report.get("errors") or []))

    def test_config_templates_roundtrip(self) -> None:
        names = list_config_templates()
        self.assertTrue(names)
        for n in names:
            tmpl = get_config_template(n)
            self.assertIsInstance(tmpl, dict)
        with self.assertRaises(KeyError):
            _ = get_config_template("does.not.exist")


if __name__ == "__main__":
    unittest.main()
