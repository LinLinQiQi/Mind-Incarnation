# Doc Map - MI Docs

Source of truth for V1 behavior: `docs/mi-v1-spec.md`

Other important docs to keep aligned:

- `README.md` (default English, public entry point)
- `README.zh-CN.md` (Chinese entry point; should mirror README structure for user-facing changes)
- `AGENTS.md` / `skills/mi-doc-keeper/*` (doc hygiene and maintenance rules)

## Update mapping

- Runtime control changes (batching, when MI asks user, closure behavior)
  - Update: `docs/mi-v1-spec.md` ("Runtime Loop", "Hard Constraints", "V1 Scope")
  - Often update: `README.md`, `README.zh-CN.md` (if user-facing CLI behavior changes)
- New/changed MI prompt(s) or semantics
  - Update: `docs/mi-v1-spec.md` ("Prompts (MI Prompt Pack)")
- New/changed config knobs (interrupt, violation response, transparency)
  - Update: `docs/mi-v1-spec.md` ("Soft-Constraint Violations", "Data Models (MindSpec)", related sections)
- Storage/logging changes (transcripts, evidence log, learned changes)
  - Update: `docs/mi-v1-spec.md` ("Transparency", "Data Models", "Doc Update Policy")
- Policy changes (values compilation, learned rule tightening, auto-answering)
  - Update: `docs/mi-v1-spec.md` ("Prompts", "Soft-Constraint Violations", "MindSpec")
- CLI commands/flags or behavior changes
  - Update: `docs/mi-v1-spec.md` ("CLI Usage (V1)")
  - Update: `README.md`, `README.zh-CN.md` (examples/quickstart)
- License / repo metadata changes
  - Update: `LICENSE` (and ensure `README.md` / `README.zh-CN.md` mention it)
- Doc hygiene / maintenance process changes
  - Update: `AGENTS.md`, `skills/mi-doc-keeper/SKILL.md`, and this file

## Minimal drift check heuristic

If any files outside `docs/` changed, ensure either:

- `docs/mi-v1-spec.md` is updated, or the final response explicitly states why it remains correct.

If any user-facing behavior (CLI, storage paths, artifacts) changed, ensure either:

- `README.md` and `README.zh-CN.md` are updated, or the final response explicitly states why they remain correct.
