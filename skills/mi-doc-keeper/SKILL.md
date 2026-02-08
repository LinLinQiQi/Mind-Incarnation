---
name: mi-doc-keeper
description: Keep Mind Incarnation (MI) documentation accurate and up to date. Use when working in the MindIncarnation repo to implement or change MI behavior, autonomy decisions, prompts, schemas, storage, risk/interrupt logic, or any other functional changes. Remind and enforce updating docs/mi-v1-spec.md in the same patch.
---

# MI Doc Keeper

## Scope guard (project-only)

This skill applies only when the current repo contains the marker file `.mi-project`.

If `.mi-project` is missing, do not apply this skill's workflow.

## Workflow (run on every functional change)

1) Identify the user-visible behavior change (even if "behavior-preserving").
2) Open and update `docs/mi-v1-spec.md` in the same patch.
3) Ensure the following stay consistent with the implementation:
   - Constraints (no tool interception, no forced step slicing)
   - Runtime loop (batch autopilot)
   - Prompt pack semantics and IO contracts
   - Data models / config knobs
   - Violation handling and learned rules
4) Run a quick doc drift check:
   - If any non-doc files changed, `docs/mi-v1-spec.md` should usually change too.
   - If it did not, explain why in the final response and confirm the spec is still correct.
5) Repo sync (when this repo has a GitHub remote configured):
   - Prefer committing logical changes with clear messages.
   - After committing, push `main` to `origin` to keep the open-source repo up to date.
   - Only prompt the user if push fails or no remote is configured.

## Reference

For a "change -> doc section" mapping and checklist, read `references/doc-map.md`.
