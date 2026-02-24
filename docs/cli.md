# MI CLI Guide (V1)

This document is a practical CLI reference. For behavioral truth (prompts, schemas, runtime loop), see `docs/mi-v1-spec.md`.

## Goals

- Keep the **common path** short (low cognitive load).
- Keep the **details** available without bloating `README.md`.

## Project Selection (Run From Anywhere)

Most commands are project-scoped. MI resolves a `project_root` automatically, but you can be explicit:

- Per-command: `--cd <project_root>` (works on most project-scoped commands)
- Global default for this invocation: `mi -C <project_root> <cmd> ...`
- Selection tokens: `@last` / `@pinned` / `@<alias>`
- Env: `$MI_CD` (path or token)

Shorthands (sugar):

- `mi @pinned status` is sugar for `mi -C @pinned status`
- `mi /path/to/repo status` is sugar for `mi -C /path/to/repo status`
- If you omit the subcommand, MI defaults to `status`:
  - `mi` -> `mi status`
  - `mi @pinned` -> `mi -C @pinned status`

Manage tokens:

```bash
mi project use --cd /path/to/your/project     # set @last
mi project pin --cd /path/to/your/project     # set @pinned
mi project unpin                              # clear @pinned
mi project alias add repo1 --cd /path/to/your/project
mi project alias list
```

## Everyday Workflow (Recommended)

Minimal daily loop:

```bash
mi                       # status (default)
mi run "Do X, then verify with minimal checks."
mi last --json           # == mi show last --json
mi hands -n 200          # == mi tail hands -n 200
```

Key entrypoints:

- `mi run ...` runs the batch autopilot above Hands (default Hands: Codex CLI).
- `mi status` is read-only and prints next-step hints (copy/pasteable).
- `mi show last` is the "front door" for the latest batch bundle.
- `mi tail ...` is the canonical tail for EvidenceLog and transcripts.

## Values / Settings (First-Time Setup)

Initialize MI and record your values (global, canonical):

```bash
mi init --values "I prefer behavior-preserving refactors. Stop when no tests exist. Avoid network/install unless necessary."
mi values show
```

Inspect and adjust operational settings (canonical: Thought DB preference claims):

```bash
mi settings show --cd /path/to/your/project
mi settings set --ask-when-uncertain ask --refactor-intent behavior_preserving
mi settings set --scope project --cd /path/to/your/project --ask-when-uncertain proceed
```

## Run

Quotes are optional; multi-word tasks work:

```bash
mi run --cd /path/to/your/project Do X then verify with minimal checks
mi run --cd /path/to/your/project "Do X, then verify with minimal checks."
```

Common flags:

- `--max-batches N`
- `--continue-hands` / `--reset-hands`
- `--quiet`
- `--hands-raw`
- `--no-mi-prompt`
- `--redact`
- `--why` (WhyTrace at run end)

## Inspect / Tail

Show a resource by id or transcript path:

```bash
mi show ev_<id> --json
mi show ev_<id> --global --json
mi show cl_<id> --json
mi show wf_<id> --json
mi show /path/to/transcript.jsonl -n 200
```

Shorthands:

```bash
mi ev_<id> --json         # == mi show ev_<id> --json
mi last --json            # == mi show last --json
mi hands -n 200           # == mi tail hands -n 200
mi mind -n 200 --jsonl    # == mi tail mind -n 200 --jsonl
```

Per-project overlay + resolved paths:

```bash
mi project show --json
mi project status --json     # read-only resolution (no @last update)
```

Tail:

```bash
mi tail -n 20
mi tail evidence -n 20 --raw
mi tail evidence --global -n 20 --json
mi tail hands -n 200
mi tail mind -n 200 --jsonl
```

## Thought DB (Claims / Nodes / Edges / Why)

Claims:

```bash
mi claim list --scope effective
mi claim show cl_<id> --json --graph --depth 2
mi claim mine
mi claim retract cl_<id>
mi claim supersede cl_<id> --text "..."
mi claim same-as cl_<dup> cl_<canonical>
```

Nodes:

```bash
mi node list --scope effective
mi node create --type decision --title "..." --text "..."
mi node show nd_<id> --json --graph --depth 2
mi node retract nd_<id>
```

Edges:

```bash
mi edge create --type depends_on --from <from_id> --to <to_id>
mi edge list --type depends_on --from ev_<id>
mi edge show ed_<id> --json
```

WhyTrace:

```bash
mi why last
mi why event ev_<id>
mi why claim cl_<id>
```

## Workflows + Host Adapters (Experimental)

Workflows (MI IR):

```bash
mi workflow create --scope project --name "My workflow"
mi workflow list --scope effective
mi workflow show wf_<id> --markdown
mi workflow edit wf_<id> --scope effective --request "Change step 2 to run tests first"
```

Host bindings + sync (derived artifacts):

```bash
mi host bind openclaw --workspace /path/to/openclaw/workspace
mi host sync
```

## Maintenance

Archive older transcripts (dry-run by default):

```bash
mi gc transcripts
mi gc transcripts --apply
```

Compact Thought DB JSONL (dry-run by default):

```bash
mi gc thoughtdb
mi gc thoughtdb --apply
mi gc thoughtdb --global --apply
```

Memory index:

```bash
mi memory index status
mi memory index rebuild
```

