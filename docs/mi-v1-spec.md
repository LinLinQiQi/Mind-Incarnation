# Mind Incarnation (MI) - V1 Spec (Batch Autopilot above Hands; default: Codex CLI)

Status: draft
Last updated: 2026-02-10

## Goal

Build a "mind layer" that sits *above* an execution agent (Hands; V1 default: Codex CLI) and reduces user burden by:

- Injecting a minimal values/preferences context into prompts ("light injection")
- Reading the agent's raw output (transcript) and deciding what to do next via MI prompts
- Autonomously answering the agent's questions when possible (using values + evidence + memory)
- Keeping a persistent evidence log to avoid context loss and to support self-evaluation of completion

## Root Idea (Keep Stable)

Mind Incarnation (MI) exists to "incarnate" a person's values, habits, and decision style into an agent-like layer that operates **above** other agents (Hands).

This section captures the most fundamental, intended-to-be-stable principles. If these change, it should be an explicit user decision (not an accidental refactor).

Non-negotiables (design intent):

- MI is a **controller**, not the executor: it only writes input prompts and reads outputs from Hands; it is not a tool proxy and does not enforce allow/deny at the command level.
- MI optimizes for **low user burden**: default is to auto-advance using "values + evidence" and answer questions on behalf of the user; MI asks the user only when it cannot proceed safely or values are unclear.
- MI is **transparent by default**: always store raw Hands transcripts + an EvidenceLog so users can audit what happened and why MI decided what it did.
- MI is **personal and tunable**: values/preferences are expressed in prompts and compiled into structured logic; MI can learn tighter rules over time, but learning must be reversible (rollback).
- MI avoids "protocol tyranny": it should not force Hands into rigid step-by-step reporting; light injection is allowed, but Hands should remain free to execute efficiently.
- When "refactor" is requested, the default intent is **behavior-preserving** unless the user explicitly asks for behavior changes.

## Hard Constraints (Aligned)

- MI MUST NOT intercept or gate Hands tool/command execution (no tool proxy / no allow/deny for shell commands).
- MI MUST NOT force Hands into step-by-step protocols (no mandatory "STEP_REPORT" schema; no artificial step slicing).
- MI MAY provide an optional "interrupt/terminate Hands process" mode for risk containment (implemented for Codex; best-effort for other CLIs).
- "Refactor" intent is **behavior-preserving by default** unless the user explicitly requests behavior changes.
- If a project has **no tests**, MI asks the user ONCE per project to pick a verification strategy, then reuses it.
- "Closed loop" completion is evaluated by MI itself (no default user acceptance prompt).

## V1 Scope

- Hands: Codex CLI by default (provider=`codex`). Experimental: wrap other agent CLIs via provider=`cli` (e.g., Claude Code) with a generic wrapper.
- Operating unit: **batch** (one MI input -> one Hands run until natural pause -> MI reads output -> decide next batch)

Non-goals for V1:

- Multi-agent routing (later)
- Hard sandboxing / permissions enforcement (later)

## Runtime Loop (Batch Autopilot)

```mermaid
flowchart TD
  U[User task + values prompt] --> S[Load MindSpec + ProjectOverlay + Learned]
  S --> I[Build light injection + task input]
  I --> C[Run Hands (free execution)]
  C --> T[Capture raw transcript + optional repo observation]
  T --> E[MI: extract_evidence]
  E --> P[MI: plan_min_checks]
  P --> AA[MI: auto_answer_to_codex (if needed)]
  AA --> ARB[MI: pre-action arbitration]
  ARB -->|answer/checks available| I
  ARB -->|need user input| Q[Ask user (minimal)]
  ARB -->|no pre-actions| D[MI: decide_next (includes closure)]
  D -->|need info not covered| Q
  D -->|continue or run checks| I
  D -->|done/blocked| END[Stop]
```

Pre-action arbitration (deterministic, V1):

- If `auto_answer_to_codex.needs_user_input=true`: ask the user with `ask_user_question`, then send the user's answer to Hands (optionally combined with minimal checks).
- Else if `plan_min_checks.needs_testless_strategy=true` and ProjectOverlay has not chosen a strategy: ask the user once per project, persist it, then re-plan checks.
- Else if `auto_answer_to_codex.should_answer=true` and/or `plan_min_checks.should_run_checks=true`: send `codex_answer_input` and/or `codex_check_input` to Hands (combined into one batch input when both exist).
- Otherwise: fall back to `decide_next`.

Additionally, when `decide_next` outputs `next_action=ask_user`, MI may attempt `auto_answer_to_codex` on the `ask_user_question` before prompting the user (to further reduce user burden).

Loop/stuck guard (deterministic, V1):

- Whenever MI is about to send a next input to Hands, it computes a bounded signature of `(codex_last_message, next_input)` (field name is legacy, but it is "Hands last message").
- If a loop-like repetition pattern is detected (AAA or ABAB), MI records `kind="loop_guard"` and either:
  - asks the user for an override instruction (if `defaults.ask_when_uncertain=true`), or
  - stops with `status=blocked` (if `defaults.ask_when_uncertain=false`).

Mind failure handling (deterministic, V1):

- If a Mind prompt-pack call fails (network/config/schema/JSON parse/etc.), MI records `kind="mind_error"` in EvidenceLog with best-effort pointers to the mind transcript.
- MI continues when possible (e.g., skip optional steps like `risk_judge` / `plan_min_checks` / `auto_answer_to_codex`), but if it cannot safely determine the next action (notably `decide_next`), MI will either:
  - ask the user for an override instruction (when `defaults.ask_when_uncertain=true`), or
  - stop with `status=blocked` (when `defaults.ask_when_uncertain=false`).

## Hands + Mind Provider Integration (V1)

MI has two provider roles:

- Hands: the execution agent MI drives (default: Codex CLI)
- Mind: the model MI uses for its internal prompt-pack decisions (default: Codex CLI with `--output-schema`)

These are configured via `config.json` under MI home (see "CLI Usage (V1)").

Hands providers:

- `hands.provider=codex` (default)
  - Uses `codex exec --json --full-auto` (and `codex exec resume --json --full-auto` for continuation).
  - Captures Codex's JSONL event stream (stdout) and logs (stderr) as the raw transcript.
- `hands.provider=cli` (experimental)
  - Runs arbitrary command argv configured by the user (wrapper mechanism).
  - Captures raw stdout/stderr lines into MI-owned JSONL transcript records.
  - Resume is optional; it depends on whether the underlying CLI supports a thread/session id.
  - Evidence/risks are best-effort: when the wrapped CLI prints JSON (e.g., Claude Code `--output-format stream-json|json`), MI will parse JSON events; otherwise it falls back to heuristically scanning captured text (paths/errors/etc.). Post-hoc risk signals are detected by scanning transcript text for risky markers.
  - Interrupt is best-effort: MI can send signals to terminate the process, but it can only trigger based on observed output text (unlike Codex which exposes `command_execution` events).

Mind providers:

- `mind.provider=codex_schema` (default)
  - Calls Codex in a separate "mind" run:
    - `codex exec --sandbox read-only --json --output-schema <schema.json>`
  - Parses the final `agent_message` as strict JSON.
- `mind.provider=openai_compatible`
  - Calls an OpenAI-compatible Chat Completions endpoint.
  - Uses local JSON Schema validation + repair retries (best-effort across vendors).
  - Works with many vendors (e.g., DeepSeek/Qwen/GLM) as long as they expose an OpenAI-compatible endpoint; configure `base_url` + `model` + API key env in `config.json`.
- `mind.provider=anthropic`
  - Calls Anthropic Messages API.
  - Uses local JSON Schema validation + repair retries.

Context isolation (important): Mind and Hands do **not** share a session/thread context by default. Mind calls run as separate requests/runs and do not reuse Hands thread state.

Schema note (Codex `--output-schema`): Codex's `--output-schema` is effectively strict. For object schemas, every key in `properties` must appear in `required`. Optional fields must be expressed via `null` (e.g., `{"anyOf":[{"type":"null"},{...}]}`), not by omitting the key.

## Transparency

MI persists and exposes:

- Raw Hands transcript (full stdout/stderr stream, timestamped)
- EvidenceLog (JSONL): per-batch evidence, plus what MI sent to Hands and any repo observations

The user can choose to view:

- MI summaries only
- or expand to see raw transcript + evidence entries

## Soft-Constraint Violations (e.g., external actions without prior clarification)

Because MI does not intercept tools, "external actions" are a **soft policy** enforced by:

- Light injection guidance ("if not covered by values, pause and ask")
- Post-hoc detection from transcript/repo changes
- Automatic "learned" tightening (reversible), plus optional immediate user escalation

Optional interrupt mode:

- MI may interrupt the Hands process when real-time transcript suggests a high-risk action is happening (implemented for Codex; other CLIs are best-effort).
- This behavior is controlled by MindSpec preferences (default can be off).

## Minimal Checks Policy (V1)

MI can propose or generate "minimal checks" when evidence is insufficient, prioritizing:

1) Existing project checks that Hands can run (tests/build/lint; default Hands: Codex)
2) Minimal new checks/tests (smoke test) **only if aligned with values/preferences**

Execution preference (aligned): **Hands runs checks** (MI only suggests/plans via next batch input).

Implementation: MI records a `check_plan` after each batch. To reduce latency/cost, MI may **skip** the `plan_min_checks` model call when evidence indicates no uncertainty/risk/questions; in that case it records a default `check_plan` with `should_run_checks=false` and a short note explaining the skip.

## Prompts (MI Prompt Pack)

MI uses the following internal prompts (all should return strict JSON):

1) `compile_mindspec` (implemented)
   - Input: user values/preferences prompt
   - Output: `MindSpec` base with:
     - `values_summary` (concise)
     - `decision_procedure` (`summary` + Mermaid flowchart)
     - concrete default knobs (interrupt/violation response/etc.)

2) `extract_evidence` (implemented)
   - Input: batch input MI sent to Hands, Hands provider hint (e.g., `codex|cli`), machine-extracted batch summary (incl. transcript event observation), and repo observation
   - Output: `EvidenceItem` with `facts`, `actions`, `results`, `unknowns`, `risk_signals`

3) `risk_judge` (implemented; post-hoc)
   - Input: Hands provider hint + recent transcript snippets + `MindSpec` + `EvidenceLog`
   - Output: risk judgement with `category`, `severity`, `should_ask_user`, `mitigation`, and optional learned tightening

4) `plan_min_checks` (implemented)
   - Input: Hands provider hint + `MindSpec`, `ProjectOverlay`, repo observation, recent `EvidenceLog`
   - Output: a minimal check plan and (when needed) a single Hands instruction (`codex_check_input`, legacy name) to execute the checks

5) `auto_answer_to_codex` (implemented)
   - Input: Hands provider hint + `MindSpec`, `ProjectOverlay`, recent `EvidenceLog`, optional minimal check plan, and the raw Hands last message (legacy prompt/schema naming uses "codex")
   - Output: an optional Hands reply (`codex_answer_input`, legacy name) that answers Hands' question(s) using values + evidence; only asks the user when MI cannot answer

6) `decide_next` (implemented)
   - Input: Hands provider hint + `MindSpec`, `ProjectOverlay`, recent `EvidenceLog`, optional risk judgement
   - Output: `NextMove` (`send_to_codex | ask_user | stop`) plus `status` (`done|not_done|blocked`). `send_to_codex` is legacy naming and means "send the next batch input to Hands". This prompt also serves as MI's closure evaluation in the default loop. Note: pre-action arbitration may already have sent an auto-answer and/or minimal checks to Hands for that batch; in that case `decide_next` may be skipped for the iteration.

Planned (not required for V1 loop to function; can be added incrementally):

- `closure_eval` (legacy/optional; not used in the default loop because `decide_next` includes closure)
- `checkpoint_decide`
- `learn_update` (beyond the simple learned text entries)

## Data Models (Minimal Schemas)

### MindSpec (layered)

MindSpec is the merge of:

- `base` (user-authored values/preferences)
- `learned` (auto-written, reversible)
- `project_overlay` (project-specific defaults; e.g., testless verification strategy)

Minimal shape:

```json
{
  "version": "v1",
  "values_text": "string",
  "values_summary": ["string"],
  "decision_procedure": {
    "summary": "string",
    "mermaid": "string"
  },
  "defaults": {
    "refactor_intent": "behavior_preserving",
    "ask_when_uncertain": true
  },
  "verification": {
    "no_tests_policy": "ask_once_per_project_then_remember"
  },
  "external_actions": {
    "network_policy": "values_judged",
    "install_policy": "values_judged"
  },
  "interrupt": {
    "mode": "off | on_high_risk | on_any_external",
    "signal_sequence": ["SIGINT", "SIGTERM", "SIGKILL"],
    "escalation_ms": [2000, 5000]
  },
  "transparency": {
    "store_raw_transcript": true,
    "store_evidence_log": true,
    "ui_expandable_transcript": true
  },
  "violation_response": {
    "auto_learn": true,
    "prompt_user_on_high_risk": true,
    "prompt_user_risk_severities": ["high", "critical"],
    "prompt_user_risk_categories": [],
    "prompt_user_respect_should_ask_user": true
  }
}
```

### ProjectOverlay

```json
{
  "project_id": "string",
  "root_path": "string",
  "identity_key": "string",
  "identity": {
    "kind": "git|path",
    "key": "string"
  },
  "stack_hints": ["string"],
  "testless_verification_strategy": {
    "chosen_once": true,
    "strategy": "string",
    "rationale": "string"
  },
  "hands_state": {
    "provider": "string",
    "thread_id": "string",
    "updated_ts": "string"
  }
}
```

### EvidenceLog (JSONL)

`evidence.jsonl` is append-only and may contain multiple record kinds:

- `hands_input` (exact MI input + light injection sent to Hands for the batch; older logs may use `codex_input`)
- `EvidenceItem` (extracted summary per batch; includes a Mind transcript pointer for `extract_evidence`)
- `mind_error` (a Mind prompt-pack call failed; includes schema/tag + error + best-effort transcript pointer)
- `risk_event` (post-hoc judgement when heuristic risk signals are present; includes a Mind transcript pointer for `risk_judge`)
- `check_plan` (minimal checks proposed post-batch; includes a Mind transcript pointer for `plan_min_checks` when planned)
- `auto_answer` (MI-generated reply to Hands questions, when possible; includes a Mind transcript pointer for `auto_answer_to_codex`; prompt/schema names are Codex-legacy)
- `decide_next` (the per-batch decision output: done/not_done/blocked + next_action + notes; includes the raw `decide_next.json` object and a Mind transcript pointer)
- `loop_guard` (repeat-pattern detection for stuck loops)
- `user_input` (answers captured when MI asks the user)
- `hands_resume_failed` (best-effort: resume by stored thread/session id failed; MI fell back to a fresh exec)

Note: EvidenceLog is append-only and may include additional record kinds in newer versions.

```json
{
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "hands_transcript_ref": "path",
  "codex_transcript_ref": "path",
  "mind_transcript_ref": "path",
  "mi_input": "string",
  "transcript_observation": {
    "event_type_counts": {"string": 0},
    "item_type_counts": {"string": 0},
    "file_paths": ["string"],
    "non_command_actions": ["string"],
    "errors": ["string"]
  },
  "repo_observation": {
    "project_root": "string",
    "stack_hints": ["string"],
    "has_tests": true,
    "test_hints": ["string"],
    "git_is_repo": true,
    "git_root": "string",
    "git_head": "string",
    "git_status_porcelain": "string",
    "git_diff_stat": "string",
    "git_diff_cached_stat": "string"
  },
  "facts": ["string"],
  "actions": [
    {"kind": "command|edit|other", "detail": "string"}
  ],
  "results": ["string"],
  "unknowns": ["string"],
  "risk_signals": ["string"]
}
```

`hands_input` record shape (what MI sent to Hands for a batch):

```json
{
  "kind": "hands_input",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "transcript_path": "path",
  "input": "string",
  "light_injection": "string",
  "prompt_sha256": "string"
}
```

`mind_error` record shape (when a Mind prompt-pack call fails):

```json
{
  "kind": "mind_error",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "schema_filename": "string",
  "tag": "string",
  "mind_transcript_ref": "path (best-effort, may be empty)",
  "error": "string"
}
```

`check_plan` record shape (minimal checks plan):

```json
{
  "kind": "check_plan",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "mind_transcript_ref": "path",
  "checks": {
    "should_run_checks": true,
    "needs_testless_strategy": false,
    "testless_strategy_question": "string",
    "check_goals": ["string"],
    "commands_hints": ["string"],
    "codex_check_input": "string",
    "notes": "string"
  }
}
```

Note: MI may emit multiple `check_plan` records within a single batch cycle (e.g., `batch_id="b0"` then `batch_id="b0.after_testless"`) when it re-plans after persisting a one-time testless verification strategy.

`user_input` record shape (captured answer):

```json
{
  "kind": "user_input",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "question": "string",
  "answer": "string"
}
```

`auto_answer` record shape (MI reply suggestion to Hands; field names are Codex-legacy):

```json
{
  "kind": "auto_answer",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "mind_transcript_ref": "path",
  "auto_answer": {
    "should_answer": true,
    "confidence": 0.0,
    "codex_answer_input": "string",
    "needs_user_input": false,
    "ask_user_question": "string",
    "unanswered_questions": ["string"],
    "notes": "string"
  }
}
```

`decide_next` record shape (per-batch decision output):

```json
{
  "kind": "decide_next",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "phase": "initial|after_user",
  "next_action": "send_to_codex|ask_user|stop",
  "status": "done|not_done|blocked",
  "confidence": 0.0,
  "notes": "string",
  "ask_user_question": "string",
  "next_codex_input": "string",
  "mind_transcript_ref": "path",
  "decision": {
    "...": "raw decide_next.json object"
  }
}
```

`loop_guard` record shape (stuck loop detection):

```json
{
  "kind": "loop_guard",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "pattern": "aaa|abab",
  "codex_last_message": "string",
  "next_input": "string",
  "reason": "string"
}
```

### Risk (post-hoc judgement)

```json
{
  "category": "network|install|push|delete|privacy|cost|other",
  "severity": "low|medium|high|critical",
  "should_ask_user": true,
  "mitigation": ["string"]
}
```

If a `risk_event` is detected, MI may immediately prompt the user to continue depending on `MindSpec.violation_response` knobs:

- `prompt_user_on_high_risk` (master switch; legacy name)
- `prompt_user_risk_severities` (which severities to prompt for)
- `prompt_user_risk_categories` (optional allow-list; empty means any)
- `prompt_user_respect_should_ask_user` (when true, prompt only if `risk_judge.should_ask_user=true`)

### Learned (append-only, reversible)

V1 stores learned preferences as JSONL records:

- Add: `{id, ts, scope, enabled=true, text, rationale}`
- Disable (rollback): `{id, ts, action="disable", target_id, rationale}`

```json
{
  "id": "string",
  "ts": "RFC3339 timestamp",
  "scope": "global|project",
  "enabled": true,
  "text": "string",
  "rationale": "string"
}
```

## Storage Layout (V1)

Default MI home: `~/.mind-incarnation` (override with `$MI_HOME` or `mi --home ...`).

- Global:
  - `mindspec/base.json`
  - `mindspec/learned.jsonl`
- Project index (stable identity -> project_id mapping):
  - `projects/index.json`
- Per project (keyed by a resolved `project_id`):
  - `projects/<project_id>/overlay.json`
  - `projects/<project_id>/learned.jsonl`
  - `projects/<project_id>/evidence.jsonl`
  - `projects/<project_id>/transcripts/hands/*.jsonl`
  - `projects/<project_id>/transcripts/mind/*.jsonl`

Note: `project_id` is legacy-compatible (historically a hash of the root path), but MI also stores an `identity_key` in ProjectOverlay and maintains a `projects/index.json` mapping so the same project can be recognized across path moves/clones (best-effort; especially effective for git repos).

## CLI Usage (V1)

All commands support `--home <dir>` to override MI storage (or set `$MI_HOME`).

Install (provides the `mi` command):

```bash
pip install -e .
```

Note: You can also run via `python3 -m mi ...` without installing.

Print MI version:

```bash
mi version
```

Provider config (Mind/Hands):

- Location: `<home>/config.json` (defaults to `~/.mind-incarnation/config.json`)
- Initialize: `mi config init` (then edit the JSON)
- View (redacted): `mi config show`
- Validate: `mi config validate` (or `mi config doctor`)
- Examples: `mi config examples`
- Template: `mi config template <name>` (prints a JSON snippet to merge into `config.json`)
- Apply template: `mi config apply-template <name>` (deep-merge into `config.json`, writes a rollback backup)
- Rollback: `mi config rollback` (restore the last apply-template backup)
- Path: `mi config path`

Key knobs (V1):

- `mind.provider`: `codex_schema | openai_compatible | anthropic`
- `hands.provider`: `codex | cli`
- `hands.continue_across_runs`: when true, MI will try to reuse the last stored Hands thread/session id across separate `mi run` invocations (best-effort)

Example: Hands = Claude Code (via `hands.provider=cli`)

MI wraps an agent CLI by capturing stdout/stderr into an MI-owned transcript. Command flags vary by tool/version; treat this as a placeholder example.

Edit `<home>/config.json`:

```json
{
  "hands": {
    "provider": "cli",
    "cli": {
      "prompt_mode": "arg",
      "exec": ["claude", "...", "{prompt}", "..."],
      "resume": ["claude", "...", "{thread_id}", "...", "{prompt}", "..."],
      "thread_id_regex": "\"session_id\"\\s*:\\s*\"([A-Za-z0-9_-]+)\"",
      "env": {}
    }
  }
}
```

Notes:

- If your Claude Code install requires env, set it in your shell (preferred) or under `hands.cli.env`.
- If your CLI can output JSON events (e.g., "stream-json"/"json"), MI will parse them (best-effort) to improve evidence extraction, last-message detection, and session id extraction.
- `thread_id_regex` is a fallback only: it extracts an id from raw text if no JSON session id is available.

Initialize/compile MindSpec:

```bash
mi --home ~/.mind-incarnation init --values "..."
```

Common init flags:

- `--show`: print `values_summary` and `decision_procedure` after compiling
- `--dry-run`: compile and print, but do not write `mindspec/base.json`
- `--no-compile`: skip model compilation and write defaults + `values_text` only

Run batch autopilot:

```bash
mi --home ~/.mind-incarnation run --cd <project_root> --show "<task>"
```

Common run flags:

- `--max-batches N`: cap the number of Hands batches
- `--continue-hands`: try to resume the last stored Hands thread/session id for this project (best-effort)
- `--reset-hands`: clear the stored Hands thread/session id for this project before running

Inspect latest batch bundle (MI input + last agent message + evidence pointers + mind transcript pointers):

```bash
mi --home ~/.mind-incarnation last --cd <project_root>
mi --home ~/.mind-incarnation last --cd <project_root> --json
mi --home ~/.mind-incarnation last --cd <project_root> --redact
```

Inspect per-project state (overlay + resolved paths):

```bash
mi --home ~/.mind-incarnation project show --cd <project_root>
mi --home ~/.mind-incarnation project show --cd <project_root> --json
mi --home ~/.mind-incarnation project show --cd <project_root> --redact
```

Note: some output keys keep legacy `codex_*` / `*_to_codex` naming for backward compatibility; they refer to Hands.

Tail EvidenceLog:

```bash
mi --home ~/.mind-incarnation evidence tail --cd <project_root> -n 20
mi --home ~/.mind-incarnation evidence tail --cd <project_root> -n 20 --raw
mi --home ~/.mind-incarnation evidence tail --cd <project_root> -n 20 --raw --redact
```

Show raw transcript (defaults to latest Hands transcript; Mind transcripts optional):

```bash
mi --home ~/.mind-incarnation transcript show --cd <project_root> -n 200
mi --home ~/.mind-incarnation transcript show --cd <project_root> --mind -n 200
mi --home ~/.mind-incarnation transcript show --cd <project_root> -n 200 --redact
```

Inspect/rollback learned preferences:

```bash
mi --home ~/.mind-incarnation learned list --cd <project_root>
mi --home ~/.mind-incarnation learned disable <id> --scope project --cd <project_root>
```

## Doc Update Policy (Source of Truth)

This spec is the source of truth for V1 behavior. Any functional changes MUST update:

- This file: `docs/mi-v1-spec.md`

Additionally, keep other user-facing docs aligned when impacted:

- `README.md` and `README.zh-CN.md` (CLI usage/examples, artifact locations, core principles)
- Any related `docs/*.md` files introduced later

Doc sections to keep aligned:

- Runtime loop / constraints (if execution contract changes)
- Prompt pack (if new prompts are added or semantics change)
- Data models (if schemas/config change)

## Implementation Plan (V1)

1) Implement an MI wrapper CLI that runs Hands as a child process (default: `codex`; optional generic `cli` wrapper) and captures transcripts (no step slicing).
2) Persist artifacts per batch: transcripts and `EvidenceLog` (JSONL).
3) Implement prompt-pack calls via `codex exec --output-schema` (strict JSON parsing with safe fallbacks) and optional API-backed Mind providers with local schema validation.
4) Add post-hoc risk monitoring + optional interrupt/terminate mode (configurable).
5) Add project memory: store "no tests" verification strategy once per project and reuse.
