# Mind Incarnation (MI)

[English](README.md) | [中文](README.zh-CN.md)

Mind Incarnation (MI) is a values-driven "mind layer" that sits **above** execution agents (V1: Codex CLI) to reduce user burden:

- Inject minimal values/preferences context ("light injection")
- Read raw agent output (full transcript) and decide what to do next
- Auto-answer the agent's questions when possible (values + evidence + memory)
- Persist an EvidenceLog to avoid context loss and support self-evaluation of completion

Status: V1 (draft), batch autopilot above Hands (default: Codex CLI).

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
- Default providers: Codex CLI installed and authenticated
- Optional: configure alternative Mind/Hands providers via `mi config` (OpenAI-compatible APIs, Anthropic, other agent CLIs)

## Install

Editable install (recommended for development):

```bash
pip install -e .
```

This provides the `mi` command (you can still use `python -m mi`).

```bash
mi version
```

## Quickstart

Initialize provider config (writes `~/.mind-incarnation/config.json` by default):

```bash
mi config init
mi config path
mi config show
```

Initialize global values/preferences (writes MindSpec to `~/.mind-incarnation/mindspec/base.json` by default):

```bash
mi init --values "My values: minimize questions; prefer behavior-preserving refactors; stop when no tests exist; avoid network/install/push unless necessary."
```

Run MI batch autopilot above Hands (stores transcripts + evidence under `~/.mind-incarnation/projects/<id>/`):

```bash
mi run --cd /path/to/your/project --show "Do X, then verify with minimal checks."
```

Inspect the latest batch (what MI sent, last agent message, evidence pointers):

```bash
mi last --cd /path/to/your/project
mi last --cd /path/to/your/project --redact
```

Tail EvidenceLog / show raw transcript:

```bash
mi evidence tail --cd /path/to/your/project -n 20
mi transcript show --cd /path/to/your/project -n 200
mi transcript show --cd /path/to/your/project -n 200 --redact
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
