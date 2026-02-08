# MindIncarnation - Agent Instructions

Scope: entire repo.

## Doc hygiene (must)

- Keep `docs/mi-v1-spec.md` accurate. Any functional change (behavior, prompts, schemas, config, runtime loop) must update this doc in the same patch.
- If a change touches "automation/advancement", "risk/interrupt", "learned rules", or "evidence logging", update the matching section in `docs/mi-v1-spec.md`.
- If the `mi-doc-keeper` skill is available, use it for changes in this repo.

## Working style

- Prefer small, reversible changes.
- If behavior intent changes (e.g., refactor not behavior-preserving), call it out explicitly in docs and user-facing output.
