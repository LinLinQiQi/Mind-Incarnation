# Doc Map - MI V1 Spec

Source of truth: `docs/mi-v1-spec.md`

## Update mapping

- Runtime control changes (batching, when MI asks user, closure behavior)
  - Update: "Runtime Loop", "Hard Constraints", "V1 Scope"
- New/changed MI prompt(s) or semantics
  - Update: "Prompts (MI Prompt Pack)"
- New/changed config knobs (interrupt, violation response, transparency)
  - Update: "Soft-Constraint Violations", "Data Models (MindSpec)", related sections
- Storage/logging changes (transcripts, evidence log, learned changes)
  - Update: "Transparency", "Data Models", "Doc Update Policy"
- Policy changes (values compilation, learned rule tightening, auto-answering)
  - Update: "Prompts", "Soft-Constraint Violations", "MindSpec"
- CLI commands/flags or behavior changes
  - Update: "CLI Usage (V1)"

## Minimal drift check heuristic

If any files outside `docs/` changed, ensure either:

- `docs/mi-v1-spec.md` is updated, or
- the final response explicitly states why the doc remains correct.
