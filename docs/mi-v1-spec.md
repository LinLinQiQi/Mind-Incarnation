# Mind Incarnation (MI) - V1 Spec (Batch Autopilot for Codex)

Status: draft
Last updated: 2026-02-08

## Goal

Build a "mind layer" that sits *above* an execution agent (V1: Codex CLI) and reduces user burden by:

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

- MI MUST NOT intercept or gate Codex tool execution (no tool proxy / no allow/deny for shell commands).
- MI MUST NOT force Codex into step-by-step protocols (no mandatory "STEP_REPORT" schema; no artificial step slicing).
- MI MAY provide an optional "interrupt/terminate Codex process" mode for risk containment.
- "Refactor" intent is **behavior-preserving by default** unless the user explicitly requests behavior changes.
- If a project has **no tests**, MI asks the user ONCE per project to pick a verification strategy, then reuses it.
- "Closed loop" completion is evaluated by MI itself (no default user acceptance prompt).

## V1 Scope

- Single Hands: Codex CLI
- Operating unit: **batch** (one MI input -> one Codex run until natural pause -> MI reads output -> decide next batch)

Non-goals for V1:

- Multi-agent routing (later)
- Hard sandboxing / permissions enforcement (later)

## Runtime Loop (Batch Autopilot)

```mermaid
flowchart TD
  U[User task + values prompt] --> S[Load MindSpec + ProjectOverlay + Learned]
  S --> I[Build light injection + task input]
  I --> C[Run Codex (free execution)]
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

- If `auto_answer_to_codex.needs_user_input=true`: ask the user with `ask_user_question`, then send the user's answer to Codex (optionally combined with minimal checks).
- Else if `plan_min_checks.needs_testless_strategy=true` and ProjectOverlay has not chosen a strategy: ask the user once per project, persist it, then re-plan checks.
- Else if `auto_answer_to_codex.should_answer=true` and/or `plan_min_checks.should_run_checks=true`: send `codex_answer_input` and/or `codex_check_input` to Codex (combined into one batch input when both exist).
- Otherwise: fall back to `decide_next`.

Additionally, when `decide_next` outputs `next_action=ask_user`, MI may attempt `auto_answer_to_codex` on the `ask_user_question` before prompting the user (to further reduce user burden).

Loop/stuck guard (deterministic, V1):

- Whenever MI is about to send a next input to Codex, it computes a bounded signature of `(codex_last_message, next_input)`.
- If a loop-like repetition pattern is detected (AAA or ABAB), MI records `kind="loop_guard"` and either:
  - asks the user for an override instruction (if `defaults.ask_when_uncertain=true`), or
  - stops with `status=blocked` (if `defaults.ask_when_uncertain=false`).

## Codex Integration (V1)

- Hands runs use `codex exec --json --full-auto` (and `codex exec resume --json --full-auto` for continuation).
- MI captures Codex's JSONL event stream (stdout) and logs (stderr) as the raw transcript.
- MI prompt-pack calls are implemented by calling Codex in a separate "mind" run:
  - `codex exec --sandbox read-only --json --output-schema <schema.json>`
  - MI parses the final `agent_message` as strict JSON.

Schema note (important): Codex's `--output-schema` is effectively strict. For object schemas, every key in `properties` must appear in `required`. Optional fields must be expressed via `null` (e.g., `{"anyOf":[{"type":"null"},{...}]}`), not by omitting the key.

## Transparency

MI persists and exposes:

- Raw Codex transcript (full stdout/stderr stream, timestamped)
- EvidenceLog (JSONL): per-batch evidence, plus what MI sent to Codex and any repo observations

The user can choose to view:

- MI summaries only
- or expand to see raw transcript + evidence entries

## Soft-Constraint Violations (e.g., external actions without prior clarification)

Because MI does not intercept tools, "external actions" are a **soft policy** enforced by:

- Light injection guidance ("if not covered by values, pause and ask")
- Post-hoc detection from transcript/repo changes
- Automatic "learned" tightening (reversible), plus optional immediate user escalation

Optional interrupt mode:

- MI may interrupt the Codex process when real-time transcript suggests a high-risk action is happening.
- This behavior is controlled by MindSpec preferences (default can be off).

## Minimal Checks Policy (V1)

MI can propose or generate "minimal checks" when evidence is insufficient, prioritizing:

1) Existing project checks that Codex can run (tests/build/lint)
2) Minimal new checks/tests (smoke test) **only if aligned with values/preferences**

Execution preference (aligned): **Codex runs checks** (MI only suggests/plans via next batch input).

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
   - Input: batch input MI sent to Codex, machine-extracted batch summary (incl. transcript event observation), and repo observation
   - Output: `EvidenceItem` with `facts`, `actions`, `results`, `unknowns`, `risk_signals`

3) `risk_judge` (implemented; post-hoc)
   - Input: recent transcript snippets + `MindSpec` + `EvidenceLog`
   - Output: risk judgement with `category`, `severity`, `should_ask_user`, `mitigation`, and optional learned tightening

4) `plan_min_checks` (implemented)
   - Input: `MindSpec`, `ProjectOverlay`, repo observation, recent `EvidenceLog`
   - Output: a minimal check plan and (when needed) a single Codex instruction (`codex_check_input`) to execute the checks

5) `auto_answer_to_codex` (implemented)
   - Input: `MindSpec`, `ProjectOverlay`, recent `EvidenceLog`, optional minimal check plan, and the raw Codex last message
   - Output: an optional Codex reply (`codex_answer_input`) that answers Codex's question(s) using values + evidence; only asks the user when MI cannot answer

6) `decide_next` (implemented)
   - Input: `MindSpec`, `ProjectOverlay`, recent `EvidenceLog`, optional risk judgement
   - Output: `NextMove` (`send_to_codex | ask_user | stop`) plus `status` (`done|not_done|blocked`). This prompt also serves as MI's closure evaluation in the default loop. Note: pre-action arbitration may already have sent an auto-answer and/or minimal checks to Codex for that batch; in that case `decide_next` may be skipped for the iteration.

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
    "prompt_user_on_high_risk": true
  }
}
```

### ProjectOverlay

```json
{
  "project_id": "string",
  "root_path": "string",
  "stack_hints": ["string"],
  "testless_verification_strategy": {
    "chosen_once": true,
    "strategy": "string",
    "rationale": "string"
  }
}
```

### EvidenceLog (JSONL)

`evidence.jsonl` is append-only and may contain multiple record kinds:

- `codex_input` (exact MI input + light injection sent to Codex for the batch)
- `EvidenceItem` (extracted summary per batch)
- `risk_event` (post-hoc judgement when heuristic risk signals are present)
- `check_plan` (minimal checks proposed post-batch)
- `auto_answer` (MI-generated reply to Codex questions, when possible)
- `loop_guard` (repeat-pattern detection for stuck loops)
- `user_input` (answers captured when MI asks the user)

```json
{
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "codex_transcript_ref": "path",
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

`codex_input` record shape (what MI sent to Codex for a batch):

```json
{
  "kind": "codex_input",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "transcript_path": "path",
  "input": "string",
  "light_injection": "string",
  "prompt_sha256": "string"
}
```

`check_plan` record shape (minimal checks plan):

```json
{
  "kind": "check_plan",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
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

`auto_answer` record shape (MI reply suggestion to Codex):

```json
{
  "kind": "auto_answer",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
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
- Per project (keyed by hash of root path):
  - `projects/<project_id>/overlay.json`
  - `projects/<project_id>/learned.jsonl`
  - `projects/<project_id>/evidence.jsonl`
  - `projects/<project_id>/transcripts/hands/*.jsonl`
  - `projects/<project_id>/transcripts/mind/*.jsonl`

## CLI Usage (V1)

All commands support `--home <dir>` to override MI storage (or set `$MI_HOME`).

Initialize/compile MindSpec:

```bash
python3 -m mi --home ~/.mind-incarnation init --values "..."
```

Common init flags:

- `--show`: print `values_summary` and `decision_procedure` after compiling
- `--dry-run`: compile and print, but do not write `mindspec/base.json`
- `--no-compile`: skip model compilation and write defaults + `values_text` only

Run batch autopilot:

```bash
python3 -m mi --home ~/.mind-incarnation run --cd <project_root> --show "<task>"
```

Inspect/rollback learned preferences:

```bash
python3 -m mi --home ~/.mind-incarnation learned list --cd <project_root>
python3 -m mi --home ~/.mind-incarnation learned disable <id> --scope project --cd <project_root>
```

## Doc Update Policy (Source of Truth)

This spec is the source of truth for V1 behavior. Any functional changes MUST update:

- This file: `docs/mi-v1-spec.md`

Doc sections to keep aligned:

- Runtime loop / constraints (if execution contract changes)
- Prompt pack (if new prompts are added or semantics change)
- Data models (if schemas/config change)

## Implementation Plan (V1)

1) Implement an MI wrapper CLI that runs `codex` as a child process and captures the JSONL event stream (no step slicing).
2) Persist artifacts per batch: transcripts and `EvidenceLog` (JSONL).
3) Implement prompt-pack calls via `codex exec --output-schema` (strict JSON parsing with safe fallbacks).
4) Add post-hoc risk monitoring + optional interrupt/terminate mode (configurable).
5) Add project memory: store "no tests" verification strategy once per project and reuse.
