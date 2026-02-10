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

## Development

Run unit tests:

```bash
make check
```

Or without `make`:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Quickstart

Initialize provider config (writes `~/.mind-incarnation/config.json` by default):

```bash
mi config init
mi config path
mi config show
mi config validate
mi config examples
mi config template mind.openai_compatible
```

Optional: Use an OpenAI-compatible API as Mind (OpenAI/DeepSeek/Qwen/GLM/etc.)

Edit `~/.mind-incarnation/config.json`:

```json
{
  "mind": {
    "provider": "openai_compatible",
    "openai_compatible": {
      "base_url": "https://api.openai.com/v1",
      "model": "<model>",
      "api_key_env": "OPENAI_API_KEY"
    }
  }
}
```

Optional: Use another agent CLI as Hands (wrapper)

MI can wrap most agent CLIs via `hands.provider=cli`. You provide the command + args for *your installed tool* (flags vary by version).

Example: Claude Code (adjust flags/args to your version)

Edit `~/.mind-incarnation/config.json`:

```json
{
  "hands": {
    "provider": "cli",
    "cli": {
      "prompt_mode": "arg",
      "exec": ["claude", "...", "{prompt}", "..."],
      "resume": ["claude", "...", "{thread_id}", "...", "{prompt}", "..."],
      "thread_id_regex": "\"session_id\"\\s*:\\s*\"([A-Za-z0-9_-]+)\""
    }
  }
}
```

Notes:

- Placeholders: `{project_root}`, `{prompt}`, `{thread_id}` (resume only).
- If your CLI can output JSON events (e.g., "stream-json"), MI will parse them (best-effort) to improve evidence extraction, session id detection, and last-message detection.

Initialize global values/preferences (writes MindSpec to `~/.mind-incarnation/mindspec/base.json` by default):

```bash
mi init --values "My values: minimize questions; prefer behavior-preserving refactors; stop when no tests exist; avoid network/install/push unless necessary."
```

Run MI batch autopilot above Hands (stores transcripts + evidence under `~/.mind-incarnation/projects/<id>/`):

```bash
mi run --cd /path/to/your/project --show "Do X, then verify with minimal checks."
```

Optional: resume/reset Hands session across runs (best-effort):

```bash
mi run --cd /path/to/your/project --continue-hands "Continue the previous work."
mi run --cd /path/to/your/project --reset-hands "Start a fresh session."
```

Inspect the latest batch (what MI sent, last agent message, evidence pointers, MI's decide_next decision, and mind transcript pointers):

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
