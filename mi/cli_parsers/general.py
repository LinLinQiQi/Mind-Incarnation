from __future__ import annotations

from typing import Any


def add_general_subparsers(*, sub: Any) -> None:
    sub.add_parser("version", help="Print MI version.")

    p_cfg = sub.add_parser("config", help="Manage MI config (Mind/Hands providers).")
    cfg_sub = p_cfg.add_subparsers(dest="config_cmd", required=True)
    p_ci = cfg_sub.add_parser("init", help="Write a default config.json to MI home.")
    p_ci.add_argument("--force", action="store_true", help="Overwrite existing config.json.")
    cfg_sub.add_parser("show", help="Show the current config (redacted).")
    cfg_sub.add_parser("validate", help="Validate the current config.json (errors + warnings).")
    cfg_sub.add_parser("examples", help="List config template names.")
    p_ct = cfg_sub.add_parser("template", help="Print a config template as JSON (merge into config.json).")
    p_ct.add_argument("name", help="Template name (see `mi config examples`).")
    p_cat = cfg_sub.add_parser("apply-template", help="Deep-merge a template into config.json (writes a rollback backup).")
    p_cat.add_argument("name", help="Template name (see `mi config examples`).")
    cfg_sub.add_parser("rollback", help="Rollback config.json to the last apply-template backup.")
    cfg_sub.add_parser("path", help="Print the config.json path.")

    p_init = sub.add_parser("init", help="Initialize global values/preferences (canonical: Thought DB).")
    p_init.add_argument(
        "--values",
        help="Values/preferences prompt text. If omitted or '-', read from stdin.",
        default="-",
    )
    p_init.add_argument(
        "--no-compile",
        action="store_true",
        help="Do not call the model; record values_set + raw values only (no derived values claims).",
    )
    p_init.add_argument(
        "--no-values-claims",
        action="store_true",
        help="Skip migrating values/preferences into global Thought DB preference/goal Claims.",
    )
    p_init.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the compiled values structure but do not write Thought DB.",
    )
    p_init.add_argument(
        "--show",
        action="store_true",
        help="Print the compiled values summary and decision procedure.",
    )

    p_values = sub.add_parser("values", help="Manage canonical values/preferences in Thought DB.")
    values_sub = p_values.add_subparsers(dest="values_cmd", required=True)
    p_vs = values_sub.add_parser("set", help="Set/update global values (writes values_set + raw claim; optional derived claims).")
    p_vs.add_argument(
        "--text",
        default="-",
        help="Values/preferences prompt text. If omitted or '-', read from stdin.",
    )
    p_vs.add_argument(
        "--no-compile",
        action="store_true",
        help="Do not call the model; record values_set + raw values only (no derived values claims).",
    )
    p_vs.add_argument(
        "--no-values-claims",
        action="store_true",
        help="Skip deriving values:base claims (still records raw values).",
    )
    p_vs.add_argument(
        "--show",
        action="store_true",
        help="Print the compiled values summary and decision procedure.",
    )
    p_vshow = values_sub.add_parser("show", help="Show the latest raw values + derived values claims.")
    p_vshow.add_argument("--json", action="store_true", help="Print as JSON.")

    p_settings = sub.add_parser("settings", help="Manage MI operational settings (canonical: Thought DB claims).")
    settings_sub = p_settings.add_subparsers(dest="settings_cmd", required=True)
    p_sshow = settings_sub.add_parser("show", help="Show resolved operational settings (project overrides global).")
    p_sshow.add_argument("--cd", default="", help="Project root used to resolve project overrides.")
    p_sshow.add_argument("--json", action="store_true", help="Print as JSON.")
    p_sset = settings_sub.add_parser("set", help="Set operational settings as canonical Thought DB claims.")
    p_sset.add_argument("--cd", default="", help="Project root used for project-scoped overrides.")
    p_sset.add_argument("--scope", choices=["global", "project"], default="global", help="Where to write the setting claims.")
    p_sset.add_argument(
        "--ask-when-uncertain",
        choices=["ask", "proceed"],
        default="",
        help="Default when MI is uncertain (canonical setting claim).",
    )
    p_sset.add_argument(
        "--refactor-intent",
        choices=["behavior_preserving", "behavior_changing"],
        default="",
        help="Default refactor intent (canonical setting claim).",
    )
    p_sset.add_argument("--dry-run", action="store_true", help="Show what would be written without writing.")


__all__ = ["add_general_subparsers"]

