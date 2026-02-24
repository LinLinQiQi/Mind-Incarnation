# Mind Incarnation (MI)

[English](README.md) | [中文](README.zh-CN.md)

Mind Incarnation (MI) is a values-driven "mind layer" that sits **above** execution agents (V1 Hands: Codex CLI) to reduce user burden:

- Inject minimal values/preferences context ("light injection")
- Read raw agent output (full transcript) and decide what to do next
- Auto-answer the agent's questions when possible (values + evidence + memory)
- Persist an EvidenceLog to avoid context loss and support self-evaluation of completion

Status: V1 (draft), batch autopilot above Hands.

## Key Principles

- Controller, not executor: MI only controls prompt input and reads output; it does **not** proxy or gate tools/commands.
- Low user burden: default is to auto-advance; ask the user only when MI cannot proceed safely.
- Transparency: always store raw transcripts + EvidenceLog for audit.
- Personal + tunable: values are prompt-authored, learning is reversible.

## Docs

- Behavior spec (source of truth): `docs/mi-v1-spec.md`
- CLI guide (practical reference): `docs/cli.md`
- Thought DB design notes: `docs/mi-thought-db.md`
- Internals (contributors): `docs/internals.md`

## Requirements

- Python 3.10+
- Default Hands: Codex CLI installed + authenticated
- Optional: configure alternative Mind/Hands providers via `mi config`

## Install

```bash
pip install -e .
mi version
```

## Quickstart (60s)

```bash
# 1) Set values (global):
mi init --values "I prefer behavior-preserving refactors. Stop when no tests exist. Avoid network/install unless necessary."

# 2) Run above your repo (path-first shorthand sets the project root):
mi /path/to/your/project run "Do X, then verify with minimal checks."

# 3) Inspect the latest batch bundle:
mi /path/to/your/project last --json

# 4) See raw Hands transcript:
mi /path/to/your/project hands -n 200
```

For the full command reference (workflows/host adapters, Thought DB, GC, memory index), see `docs/cli.md`.

## Development

```bash
make check
make doccheck
```

## Storage

By default MI writes under `~/.mind-incarnation/` (transcripts, EvidenceLog, Thought DB, indexes). Exact layout is specified in `docs/mi-v1-spec.md`.

## License

MIT. See `LICENSE`.

