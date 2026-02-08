# Mind Incarnation (MI)

[English](README.md) | [中文](README.zh-CN.md)

Mind Incarnation (MI) is a values-driven "mind layer" that sits **above** execution agents (V1: Codex CLI) to reduce user burden:

- Inject minimal values/preferences context ("light injection")
- Read raw agent output (full transcript) and decide what to do next
- Auto-answer the agent's questions when possible (values + evidence + memory)
- Persist an EvidenceLog to avoid context loss and support self-evaluation of completion

Status: V1 (draft), batch autopilot for Codex.

## Key Principles

- Controller, not executor: MI only controls prompt input and reads output; it does **not** proxy or gate tools/commands.
- No protocol tyranny: MI should not force the underlying agent into rigid step-by-step reporting.
- Low user burden: default is to auto-advance; ask the user only when MI cannot proceed safely.
- Transparency: always store raw transcripts + EvidenceLog for audit.
- Personal + tunable: values are prompt-authored and compiled into structured logic; learning is reversible.

## Docs

- V1 spec (source of truth): `docs/mi-v1-spec.md`

## Requirements

- Python 3.10+
- Codex CLI installed and authenticated

## Quickstart

Initialize global values/preferences (writes MindSpec to `~/.mind-incarnation/mindspec/base.json` by default):

```bash
python3 -m mi init --values "My values: minimize questions; prefer behavior-preserving refactors; stop when no tests exist; avoid network/install/push unless necessary."
```

Run MI batch autopilot above Codex (stores transcripts + evidence under `~/.mind-incarnation/projects/<id>/`):

```bash
python3 -m mi run --cd /path/to/your/project --show "Do X, then verify with minimal checks."
```

Inspect the latest batch (what MI sent, last agent message, evidence pointers):

```bash
python3 -m mi last --cd /path/to/your/project
```

Tail EvidenceLog / show raw transcript:

```bash
python3 -m mi evidence tail --cd /path/to/your/project -n 20
python3 -m mi transcript show --cd /path/to/your/project -n 200
```

## What You Get

- Raw Hands transcript: `~/.mind-incarnation/projects/<id>/transcripts/hands/*.jsonl`
- Mind transcripts (MI prompt-pack calls): `~/.mind-incarnation/projects/<id>/transcripts/mind/*.jsonl`
- EvidenceLog (append-only): `~/.mind-incarnation/projects/<id>/evidence.jsonl`

## Non-Goals (V1)

- Multi-agent routing
- Hard permission enforcement / tool-level gating

## License

MIT. See `LICENSE`.
