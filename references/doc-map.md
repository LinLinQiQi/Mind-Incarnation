# MI Doc Map (V1)

This file exists to prevent documentation drift. It maps common change types to the
docs that must be updated in the same patch.

If you add a new doc file, extend this map.

## Always Update

If you change any user-visible MI behavior (loop, prompts, schemas, storage, safety),
**always** update:

- `docs/mi-v1-spec.md` (source of truth)

Then update any other impacted docs (usually `README.md`, `README.zh-CN.md`, and/or
other files under `docs/`).

## Change Type -> Docs Checklist

### Runtime Loop / Autopilot Behavior

Examples: batching rules, stop/blocked semantics, loop-guard behavior, Mind circuit
breaker, check planning heuristics, auto-answer behavior.

- Update: `docs/mi-v1-spec.md` (runtime loop + relevant sections)
- If `mi show last` / `mi run` output changed: update `README.md`, `README.zh-CN.md`
- If new record kinds/fields were added: update `docs/mi-v1-spec.md` EvidenceLog section

### Prompt Pack / Schemas

Examples: add/remove prompt, change prompt semantics, schema enum/required fields.

- Update: `docs/mi-v1-spec.md` Prompts section
- Update: `docs/mi-v1-spec.md` data model snippets (MindSpec/ProjectOverlay/EvidenceLog)
- If users need to take action (new knobs/commands): update `README.md`, `README.zh-CN.md`

### EvidenceLog Records

Examples: new `kind=...`, renamed fields, new transcript pointers.

- Update: `docs/mi-v1-spec.md` EvidenceLog kinds list + any relevant record shapes
- Update: `mi/runtime/inspect.py` summarization and `mi show last` output if required (code)
- Update: `README.md`, `README.zh-CN.md` if the new record kind is user-actionable

### Learned Preferences / Memory

Examples: new learned semantics, auto-learn gating, new CLI for applying/rolling back,
new storage files.

- Update: `docs/mi-v1-spec.md` Learned section + CLI section
- If memory architecture changes (recall backends, long-term reasoning stores): update `docs/mi-thought-db.md` (design notes)
- Update: `README.md`, `README.zh-CN.md` with new commands/examples

### Risk / Interrupt / External Actions

Examples: new risk categories/signals, interrupt markers, escalation rules, default
preferences.

- Update: `docs/mi-v1-spec.md` Risk section
- Update: `docs/mi-v1-spec.md` Optional interrupt mode section
- If templates/examples change: update `README.md`, `README.zh-CN.md`

### Workflows / Host Adapters (Derived Artifacts)

Examples: workflow IR fields, mining/solidification thresholds, host binding config,
derived artifact layout, adapter implementations (e.g., OpenClaw skills generation),
auto-sync behavior.

- Update: `docs/mi-v1-spec.md` workflows + host bindings sections
- Update: `docs/mi-v1-spec.md` Storage Layout section (if new files/dirs are added)
- If users need to configure or invoke new commands: update `README.md`, `README.zh-CN.md`

### Storage Layout / Project Identity

Examples: new files under `~/.mind-incarnation`, new indices, transcript GC/archive,
project_id resolution changes.

- Update: `docs/mi-v1-spec.md` Storage Layout section
- Update: `README.md`, `README.zh-CN.md` "What You Get" section if paths change

### Provider Config / Templates (Mind/Hands)

Examples: new provider, new template, config knobs, validation behavior changes.

- Update: `README.md`, `README.zh-CN.md` provider config sections
- Update: `docs/mi-v1-spec.md` CLI usage / config section if surface area changed
- Update: `mi/config.py` templates list (code) and `mi config validate` behavior if needed

### CLI UX

Examples: new subcommand/flag, renamed output keys, new JSON fields.

- Update: `README.md`, `README.zh-CN.md` quickstart usage snippets
- Update: `docs/cli.md`, `docs/cli.zh-CN.md` (canonical CLI reference)
- Update: `docs/mi-v1-spec.md` CLI Usage section (especially if new commands/flags)

## Quick Doc Drift Checks (before committing)

- If any non-doc file changed, `docs/mi-v1-spec.md` should usually change too.
- If CLI UX changed, `README.md` and `README.zh-CN.md` should usually change too.
- If storage/record shape changed, update the relevant JSON snippets in `docs/mi-v1-spec.md`.
