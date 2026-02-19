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
- Workflows + host adapters (experimental; includes OpenClaw Skills-only target): see `docs/mi-v1-spec.md`

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

Best-effort doc drift check (warning-only by default; set `MI_DOCCHECK_STRICT=1` to fail on warnings):

```bash
make doccheck
```

Note: CI runs doccheck in strict mode.

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
mi config apply-template mind.openai_compatible
mi config rollback
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

Example: Claude Code (headless + stream-json; adjust flags to your version)

Edit `~/.mind-incarnation/config.json`:

```json
{
  "hands": {
    "provider": "cli",
    "cli": {
      "prompt_mode": "arg",
      "exec": ["claude", "-p", "{prompt}", "--output-format", "stream-json"],
      "resume": ["claude", "-p", "{prompt}", "--output-format", "stream-json", "--resume", "{thread_id}"],
      "thread_id_regex": "\"(?:session_id|sessionId)\"\\s*:\\s*\"([A-Za-z0-9_-]+)\""
    }
  }
}
```

Notes:

- Placeholders: `{project_root}`, `{prompt}`, `{thread_id}` (resume only).
- `-p` runs headless/non-interactive (recommended for wrappers).
- If your CLI can output JSON (e.g., `--output-format stream-json` or `json`), MI will parse it (best-effort) to improve evidence extraction, session id detection, and last-message detection.
- If you want MI to resume the last Claude Code session across separate `mi run` invocations, set `hands.continue_across_runs=true`.

Set global values/preferences (canonical: Thought DB):

```bash
mi values set --text "My values: minimize questions; prefer behavior-preserving refactors; stop when no tests exist; avoid network/install/push unless necessary."
mi init --values "..."  # shortcut
mi values show
```

Notes:

- `mi init` / `mi values set` appends a global EvidenceLog `values_set` event under `~/.mind-incarnation/global/evidence.jsonl` (stable `event_id` provenance).
- It also writes a raw values preference Claim tagged `values:raw` (audit). When compilation succeeds (i.e., not `--no-compile`), it also writes a global Summary node tagged `values:summary` (human-facing).
- Unless `--no-compile` or `--no-values-claims` is set, it also derives canonical values into global Thought DB preference/goal Claims tagged `values:base`, which `mi run` treats as canonical during `decide_next`.

Operational settings (canonical: Thought DB):

```bash
mi settings show --cd /path/to/your/project
mi settings set --ask-when-uncertain ask --refactor-intent behavior_preserving
mi settings set --scope project --cd /path/to/your/project --ask-when-uncertain proceed
```

Run MI batch autopilot above Hands (stores transcripts + evidence under `~/.mind-incarnation/projects/<id>/`):

```bash
# Quotes are optional; multi-word tasks work:
mi run --cd /path/to/your/project Do X then verify with minimal checks
mi run --cd /path/to/your/project "Do X, then verify with minimal checks."
```

Notes:

- `mi run` prints a live stream by default:
  - `[mi]` MI stage/decision logs
  - `[mi->hands]` the exact prompt MI sends to Hands (light injection + batch_input)
  - `[hands]` rendered Hands output (use `--hands-raw` for raw capture)
- Use `--quiet` for scripts/CI, `--no-mi-prompt` to hide MI->Hands prompt, and `--redact` for best-effort safe display.

Everyday status + front-door inspect (reduce command surface area):

```bash
mi status --cd /path/to/your/project
mi status --cd /path/to/your/project --json

# Show an MI resource by id (ev_/cl_/nd_/wf_/ed_) or a transcript path:
mi show ev_<id> --cd /path/to/your/project --json
mi show ev_<id> --global --json
mi show cl_<id> --cd /path/to/your/project --json
mi show wf_<id> --cd /path/to/your/project --json
mi show /path/to/transcript.jsonl -n 200

# Convenience pseudo-refs (delegate to existing commands):
mi show last --cd /path/to/your/project --json
mi show project --cd /path/to/your/project --json
mi show hands --cd /path/to/your/project -n 200
mi show mind --cd /path/to/your/project -n 200

# List resources (aliases for claim/node/edge/workflow list):
mi ls claims --cd /path/to/your/project
mi ls nodes --cd /path/to/your/project
mi ls workflows --cd /path/to/your/project

# Edit workflows (alias for `mi workflow edit`):
mi edit wf_<id> --cd /path/to/your/project --request "..."
```

Optional: run one WhyTrace at run end (opt-in; writes `kind=why_trace` and may materialize `depends_on` edges):

```bash
mi run --cd /path/to/your/project --why "Do X, then verify with minimal checks."
```

Notes on `--cd`:

- Most project-scoped commands accept `--cd <project_root>` to choose which project to operate on.
- You can also set a per-invocation default project root via `mi -C <project_root> <cmd> ...` (argparse: `-C/--cd` must appear before the subcommand). Subcommand `--cd` overrides `-C/--cd` if both are provided.
- You can force the project root to be the current working directory (even inside a git repo) via `mi --here <cmd> ...` (global flag; must appear before the subcommand). Useful for monorepo subdirs and ignored when `--cd/-C` is provided.
- `--cd` is optional:
  - Inside a git repo: MI defaults to the git toplevel (repo root) unless the current directory was previously used as a distinct MI project root (monorepo subproject).
  - Outside git: MI uses `@pinned` (if recorded), otherwise `@last` (if recorded), otherwise uses the current directory.
- You can set `$MI_CD` (a path or `@last/@pinned/@alias`) to run MI commands from anywhere without repeating `--cd`/`-C`.
- You can also use selection tokens:
  - `--cd @last` / `--cd @pinned` / `--cd @<alias>`
  - Manage them via `mi project use`, `mi project pin/unpin`, `mi project alias add/rm/list`
- `runtime.project_selection.auto_update_last` controls whether project-scoped commands update `@last` automatically (default: true).

Optional: resume/reset Hands session across runs (best-effort):

```bash
mi run --cd /path/to/your/project --continue-hands "Continue the previous work."
mi run --cd /path/to/your/project --reset-hands "Start a fresh session."
```

Inspect the latest batch (what MI sent, last agent message, evidence pointers, MI's decide_next decision, mind transcript pointers, and any `learn_suggested` ids):

```bash
mi show last --cd /path/to/your/project
mi show last --cd /path/to/your/project --redact
```

Note: `mi show last --json` (alias of `mi last --json`) includes `why_trace` / `why_traces` when present (e.g., from `mi run --why`), includes `state_corrupt_recent` when MI had to quarantine a corrupt state file, and includes `loop_guard` and `loop_break` when MI detects and tries to break a stuck repetition loop. You can control low-level state warning stderr printing via `MI_STATE_WARNINGS_STDERR=1` (force) / `0` (silence).

Show per-project overlay + resolved storage paths:

```bash
mi show project --cd /path/to/your/project
mi show project --cd /path/to/your/project --json
mi show project --cd /path/to/your/project --redact
```

Show how MI resolves the project root (read-only; does not update `@last`):

```bash
mi project status
mi project status --json
mi --here project status --json
```

Project selection shortcuts (`@last/@pinned/@alias`):

```bash
mi project use --cd /path/to/your/project
mi project pin --cd /path/to/your/project
mi project unpin
mi project alias add repo1 --cd /path/to/your/project
mi project alias list

mi run --cd @repo1 "Do X, then verify with minimal checks."
```

Tail EvidenceLog / show raw transcript:

```bash
mi evidence tail --cd /path/to/your/project -n 20
mi show <event_id> --cd /path/to/your/project
mi show <event_id> --cd /path/to/your/project --redact
mi show hands --cd /path/to/your/project -n 200
mi show hands --cd /path/to/your/project -n 200 --redact
mi show mind --cd /path/to/your/project -n 200
```

Optional: archive older transcripts (gzip + stubs; default is dry-run):

```bash
mi gc transcripts --cd /path/to/your/project
mi gc transcripts --cd /path/to/your/project --apply
```

Optional: compact Thought DB JSONL (archive + rewrite; default is dry-run):

```bash
mi gc thoughtdb --cd /path/to/your/project
mi gc thoughtdb --cd /path/to/your/project --apply

mi gc thoughtdb --global
mi gc thoughtdb --global --apply
```

Preference tightening (reversible; strict Thought DB mode):

```bash
# Apply a recorded suggestion (when auto-learn is off, or for manual control):
mi claim apply-suggested <suggestion_id> --cd /path/to/your/project

# Inspect + rollback canonical preference claims:
mi claim list --cd /path/to/your/project --scope effective
mi claim retract <claim_id> --cd /path/to/your/project --scope project
```

Note: `learn_suggested` suggestions are always recorded in EvidenceLog (`kind=learn_suggested`). If `violation_response.auto_learn=true` (default), MI also materializes them as Thought DB preference Claims (`applied_claim_ids`). If false, use `mi claim apply-suggested ...` to apply them later.

Experimental: preference mining

- If `config.runtime.preference_mining.auto_mine=true` (default), MI may call `mine_preferences` at LLM-judged checkpoints during `mi run` (including at run end) and may emit `kind=learn_suggested` after repeated occurrences (see `docs/mi-v1-spec.md`).

Experimental: Thought DB (atomic Claims + Nodes)

MI can maintain an append-only "Thought DB" of atomic reusable `Claim`s (fact/preference/assumption/goal), with provenance that cites **EvidenceLog `event_id` only**.

- If `config.runtime.thought_db.auto_mine=true` (default), MI may call `mine_claims` at checkpoints during `mi run` and records `kind=claim_mining`.
- If `config.runtime.thought_db.auto_materialize_nodes=true` (default), MI may also materialize `Decision` / `Action` / `Summary` nodes at checkpoints (deterministic; no extra model calls) and records `kind=node_materialized`.
- Memory index: Thought DB `claim` / `node` items are also indexable for text recall. Default `cross_project_recall.include_kinds` is Thought-DB-first (`snapshot` / `workflow` / `claim` / `node`).
- Claims are stored per project (and optionally global) and can be managed via CLI:

```bash
mi claim list --cd /path/to/your/project --scope effective
mi claim list --cd /path/to/your/project --scope effective --type preference --tag values:base --contains "tests"
mi claim show <claim_id> --cd /path/to/your/project
mi claim show <claim_id> --cd /path/to/your/project --json --graph --depth 2
mi claim mine --cd /path/to/your/project
mi claim retract <claim_id> --cd /path/to/your/project
mi claim supersede <claim_id> --cd /path/to/your/project --text "..."
mi claim same-as <dup_id> <canonical_id> --cd /path/to/your/project
```

Nodes (Decision/Action/Summary):

```bash
mi node list --cd /path/to/your/project --scope effective
mi node create --cd /path/to/your/project --scope project --type decision --title "..." --text "..."
mi node show <node_id> --cd /path/to/your/project
mi node show <node_id> --cd /path/to/your/project --json --graph --depth 2
mi node retract <node_id> --cd /path/to/your/project
```

Edges:

```bash
mi edge create --cd /path/to/your/project --scope project --type depends_on --from <from_id> --to <to_id>
mi edge list --cd /path/to/your/project --scope project
mi edge list --cd /path/to/your/project --scope project --type depends_on --from <event_id>
mi edge show <edge_id> --cd /path/to/your/project
```

Root-cause tracing (WhyTrace):

```bash
mi why last --cd /path/to/your/project
mi why event <event_id> --cd /path/to/your/project
mi why claim <claim_id> --cd /path/to/your/project
```

## Workflows + Host Adapters (Experimental)

Workflows are reusable procedures that can be **project-scoped** or **global**. MI exports the project's **effective** enabled workflows (project + global with project precedence) into host workspaces (derived artifacts).

In `mi run`:

- If an enabled workflow matches the task (`trigger.mode=task_contains`), MI injects it into the first batch input.
- If a workflow is active, MI maintains a best-effort step cursor in `ProjectOverlay.workflow_run` (does not force step-by-step reporting).
- If `config.runtime.workflows.auto_mine=true` (default), MI may call `suggest_workflow` at LLM-judged checkpoints during `mi run` (including at run end) and may solidify a repeated workflow.

Create/edit workflows:

```bash
mi workflow create --cd /path/to/your/project --scope project --name "My workflow"
mi workflow create --cd /path/to/your/project --scope global --name "My global workflow"
mi workflow list --cd /path/to/your/project --scope effective
mi workflow show <workflow_id> --cd /path/to/your/project --markdown
mi workflow edit <workflow_id> --cd /path/to/your/project --scope effective --request "Change step 2 to run tests"

# Per-project override for a global workflow:
mi workflow disable <workflow_id> --cd /path/to/your/project --scope global --project-override
mi workflow edit <workflow_id> --cd /path/to/your/project --scope global --project-override --request "Patch step s2 to run tests first"
mi workflow delete <workflow_id> --cd /path/to/your/project --scope global --project-override
```

Bind and sync an OpenClaw workspace (Skills-only target):

```bash
mi host bind openclaw --workspace /path/to/openclaw/workspace --cd /path/to/your/project
mi host sync --cd /path/to/your/project
```

Notes:

- MI writes derived artifacts under `/path/to/openclaw/workspace/.mi/generated/openclaw/...` (regeneratable).
- MI registers each generated skill dir into `/path/to/openclaw/workspace/skills/<skill_dir>` as a symlink (best-effort, reversible).

## What You Get

- Raw Hands transcript: `~/.mind-incarnation/projects/<id>/transcripts/hands/*.jsonl`
- Mind transcripts (MI prompt-pack calls): `~/.mind-incarnation/projects/<id>/transcripts/mind/*.jsonl`
- EvidenceLog (append-only; includes `snapshot` + `cross_project_recall` kinds): `~/.mind-incarnation/projects/<id>/evidence.jsonl`
- Global EvidenceLog (append-only; values/preferences lifecycle events such as `values_set`): `~/.mind-incarnation/global/evidence.jsonl`
- Thought DB (append-only Claims/Edges/Nodes): `~/.mind-incarnation/projects/<id>/thoughtdb/{claims,edges,nodes}.jsonl` and `~/.mind-incarnation/thoughtdb/global/{claims,edges,nodes}.jsonl`
- Memory text index (materialized view; rebuildable; default backend=`sqlite_fts`): `~/.mind-incarnation/indexes/memory.sqlite`

Memory index maintenance:

```bash
mi memory index status
mi memory index rebuild
```

Advanced: override memory backend via `$MI_MEMORY_BACKEND` (default `sqlite_fts`; `in_memory` is ephemeral).

## Non-Goals (V1)

- Multi-agent routing
- Hard permission enforcement / tool-level gating

## License

MIT. See `LICENSE`.
