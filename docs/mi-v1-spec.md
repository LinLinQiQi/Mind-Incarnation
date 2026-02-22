# Mind Incarnation (MI) - V1 Spec (Batch Autopilot above Hands; default: Codex CLI)

Status: draft
Last updated: 2026-02-22

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
- MI is **host-decoupled**: MI's source of truth (values, evidence, workflows) lives in MI storage; anything written into a host workspace is a derived, regeneratable artifact.
- MI avoids "protocol tyranny": it should not force Hands into rigid step-by-step reporting; light injection is allowed, but Hands should remain free to execute efficiently.
- When "refactor" is requested, the default intent is **behavior-preserving** unless the user explicitly asks for behavior changes.

## Hard Constraints (Aligned)

- MI MUST NOT intercept or gate Hands tool/command execution (no tool proxy / no allow/deny for shell commands).
- MI MUST NOT force Hands into step-by-step protocols (no mandatory "STEP_REPORT" schema; no artificial step slicing).
- MI MAY provide an optional "interrupt/terminate Hands process" mode for risk containment (implemented for Codex; best-effort for other CLIs).
- MI MAY write derived artifacts into host workspaces (e.g., Skills) but MUST keep them inside an MI-owned generated directory and keep the write layer decoupled and reversible.
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
  U[User task + values prompt] --> S[Load RuntimeConfig + ProjectOverlay + Thought DB]
  S --> I[Build light injection + task input]
  I --> C[Run Hands (free execution)]
  C --> T[Capture raw transcript + optional repo observation]
  T --> E[MI: extract_evidence]
  E --> R[MI: cross_project_recall (on-demand)]
  R --> WF[MI: workflow_progress (if workflow active)]
  WF --> P
  R --> P[MI: plan_min_checks]
  P --> AA[MI: auto_answer_to_hands (if needed)]
  AA --> ARB[MI: pre-action arbitration]
  ARB -->|answer/checks available| I
  ARB -->|need user input| Q[Ask user (minimal)]
  ARB -->|no pre-actions| TDB[MI: build Thought DB context (deterministic)]
  TDB --> D[MI: decide_next (includes closure)]
  D -->|need info not covered| Q
  D -->|continue or run checks| I
  D -->|done/blocked| END[Stop]
```

Internal wiring (behavior-preserving): `run_autopilot` now builds a `RunSession` context and delegates run-loop/pipeline/checkpoint composition to `RunLoopOrchestrator` (`mi/runtime/autopilot/orchestrator.py`). Thought DB prompt-context assembly and run-end WhyTrace candidate retrieval are routed through `ThoughtDbApplicationService` (`mi/thoughtdb/app_service.py`) as a shared facade. Phase semantics and user-visible loop behavior remain unchanged.

Pre-action arbitration (deterministic, V1):

- If `auto_answer_to_hands.needs_user_input=true`: ask the user with `ask_user_question`, then send the user's answer to Hands (optionally combined with minimal checks).
- Else if `plan_min_checks.needs_testless_strategy=true` and there is no canonical testless strategy preference Claim (tagged `mi:testless_verification_strategy`): ask the user once per project, store it as a project-scoped Thought DB preference Claim (canonical) and mirror it into ProjectOverlay (derived), then re-plan checks.
- Else if `auto_answer_to_hands.should_answer=true` and/or `plan_min_checks.should_run_checks=true`: send `hands_answer_input` and/or `hands_check_input` to Hands (combined into one batch input when both exist).
- Otherwise: fall back to `decide_next`.

Additionally, when `decide_next` outputs `next_action=ask_user`, MI may attempt `auto_answer_to_hands` on the `ask_user_question` before prompting the user (to further reduce user burden).

Workflow cursor (best-effort, V1):

- When a workflow is triggered, MI initializes `ProjectOverlay.workflow_run` as an internal cursor (active workflow id + completed/next step ids).
- After each Hands batch (and when Mind circuit allows), MI calls `workflow_progress` to infer step completion from evidence and update the cursor.
- Hands remains free to complete multiple steps in one batch; MI only tries to infer progress and provide better next-step context to `decide_next`.

Cross-project recall (on-demand, V1):

- Default: **enabled but conservative** (no embeddings required). Uses only MI-owned stores: `snapshot` + `workflow` + canonical Thought DB items (`claim` / `node`), searched by text.
- Trigger points (default): once at run start, before MI asks the user, and when risk signals are detected.
- Output is recorded as `kind="cross_project_recall"` in EvidenceLog and is included in `recent_evidence` for later Mind prompts.
- Recall query is compacted into safe text tokens (no embeddings). EvidenceLog includes `query_raw`, `query_compact`, and `tokens_used` for auditability.
- MI maintains a best-effort materialized text index under `<home>/indexes/memory.sqlite` (materialized view; source of truth remains MI logs/stores). Rebuild via `mi memory index rebuild`.
- Index sync prunes disabled workflows and orphaned project-scoped structured items (best-effort) so rolled-back artifacts don't reappear in recall.

Thought DB context (always-on, deterministic, V1):

- Before each `decide_next` (and the post-user re-decide), MI builds a small Thought DB context (no extra model calls):
  - `nodes`: recent active canonical Thought DB nodes (`Decision` / `Action` / `Summary`), including the latest global values summary node (when present)
  - `values_claims`: active global preference/goal claims tagged `values:base` (canonical values)
  - `pref_goal_claims`: other preference/goal claims (project first, then global), including pinned operational default claims (e.g., tags `mi:setting:ask_when_uncertain`, `mi:setting:refactor_intent`, `mi:testless_verification_strategy`)
  - `query_claims`: query-seeded active claims (excluding the above), retrieved deterministically using:
    - Memory text index (FTS) as a **candidate generator** (scoped to current project + global), using the compacted query tokens, and
    - a conservative fallback token scan when memory search is unavailable/insufficient.
    - Then a 1-hop edge expansion may add direct neighbor claims/nodes (`depends_on/supports/contradicts/derived_from/mentions/supersedes/same_as`) within the remaining budgets (active + valid only).
  - `edges`: a small set of reasoning/provenance edges adjacent to included claim/node ids (and recent EvidenceLog `event_id`s for provenance)
- This context is passed to the `decide_next` prompt as `thought_db_context` and should be treated as canonical when deciding (including over any raw values prompt text (`values:raw`) when conflicts arise).

Loop/stuck guard (deterministic, V1):

- Whenever MI is about to send a next input to Hands, it computes a bounded signature of `(hands_last_message, next_input)`.
- If a loop-like repetition pattern is detected (AAA or ABAB), MI records `kind="loop_guard"` and then attempts a best-effort **loop break**:
  - MI calls `loop_break` (Mind prompt-pack; output schema `loop_break.json`) and records the result as `kind="loop_break"`.
  - Preferred actions: rewrite the next instruction or force a minimal check run (without imposing step-by-step reporting).
  - If the loop break cannot safely proceed:
    - If the effective `ask_when_uncertain=true` (canonical Thought DB preference Claim tagged `mi:setting:ask_when_uncertain`), MI may ask the user for an override instruction.
    - Otherwise, MI stops with `status=blocked`.

Checkpointing (segments; internal, V1):

- MI maintains a compact internal "segment buffer" while `mi run` continues across multiple Hands batches.
- Checkpointing is enabled whenever any checkpoint-based feature is enabled, including:
  - workflow mining (`config.runtime.workflows.auto_mine=true`)
  - preference mining (`config.runtime.preference_mining.auto_mine=true`)
  - Thought DB claim mining (`config.runtime.thought_db.auto_mine=true`)
  - Thought DB deterministic node materialization (`config.runtime.thought_db.auto_materialize_nodes=true`)
- Before sending the next batch input to Hands (and once again when the run ends), MI calls `checkpoint_decide` to judge whether a segment boundary exists.
- When `checkpoint_decide.should_checkpoint=true`, MI may mine workflows and/or preferences using only the current segment evidence, **writes a compact `snapshot` record** (traceable to the segment), then resets the segment buffer for the next phase.
- Internal implementation note (behavior-preserving): segment state IO and compact-record shaping are modularized in `mi/runtime/autopilot/segment_state.py`; runtime semantics and stored artifact contracts are unchanged.
- Internal implementation note (behavior-preserving): checkpoint decision/orchestration is modularized in `mi/runtime/autopilot/checkpoint_pipeline.py`; checkpoint workflow/preference mining helpers are modularized in `mi/runtime/autopilot/checkpoint_mining.py`; deterministic checkpoint node materialization is modularized in `mi/runtime/autopilot/node_materialize.py`; runtime semantics and stored artifact contracts are unchanged.
- This mechanism exists to avoid tying workflow solidification to "user exits" and to support long-running sessions without forcing Hands into step-by-step protocols.

Mind failure handling (deterministic, V1):

- If a Mind prompt-pack call fails (network/config/schema/JSON parse/etc.), MI records `kind="mind_error"` in EvidenceLog with best-effort pointers to the mind transcript.
- If Mind fails repeatedly (default: 2 consecutive failures), MI opens a simple circuit breaker:
  - records `kind="mind_circuit"` (`state="open"`) once, and
  - skips further Mind calls for the remainder of the current `mi run` invocation (to reduce repeated `mind_error` noise).
- MI continues when possible (e.g., skip optional steps like `risk_judge` / `plan_min_checks` / `auto_answer_to_hands`), but if it cannot safely determine the next action (notably `decide_next`), MI will either:
  - ask the user for an override instruction (when the effective `ask_when_uncertain=true`, canonically stored as a Thought DB preference Claim tagged `mi:setting:ask_when_uncertain`), or
  - stop with `status=blocked` (when the effective `ask_when_uncertain=false`).

## Hands + Mind Provider Integration (V1)

MI has two provider roles:

- Hands: the execution agent MI drives (default: Codex CLI)
- Mind: the model MI uses for its internal prompt-pack decisions (default: Codex CLI with `--output-schema`)

These are configured via `config.json` under MI home (see "CLI Usage (V1)").

Hands providers:

- `hands.provider=codex` (default)
  - Uses `codex exec --json --full-auto` (and `codex exec resume --json --full-auto` for continuation).
  - Captures Codex's JSONL event stream (stdout) and logs (stderr) as the raw transcript.
  - During `mi run`, MI can live-render this stream to the terminal (prefix `[hands]`). Use `mi run --hands-raw` to display the raw JSON event lines instead.
  - Implementation note (behavior-preserving): `mi/providers/codex_runner.py` centralizes shared `exec`/`resume` option assembly to reduce drift.
  - Implementation note (behavior-preserving): interrupt config + signal escalation helpers are shared via `mi/providers/interrupts.py` (also used by `hands.provider=cli`).
- `hands.provider=cli` (experimental)
  - Runs arbitrary command argv configured by the user (wrapper mechanism).
  - Captures raw stdout/stderr lines into MI-owned JSONL transcript records.
  - During `mi run`, MI can tee captured stdout/stderr lines to the terminal (prefix `[hands:stdout]` / `[hands:stderr]`).
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
  - Response shape requirement: MI expects `choices[0].message.content` (string) to contain the JSON output. Legacy `choices[0].text` and "Responses API" payload shapes are not supported.
  - Works with many vendors (e.g., DeepSeek/Qwen/GLM) as long as they expose an OpenAI-compatible endpoint; configure `base_url` + `model` + API key env in `config.json`.
- `mind.provider=anthropic`
  - Calls Anthropic Messages API.
  - Uses local JSON Schema validation + repair retries.
  - Response shape requirement: MI expects `content[]` text blocks (Messages API). Legacy `completion` payloads are not supported.

Context isolation (important): Mind and Hands do **not** share a session/thread context by default. Mind calls run as separate requests/runs and do not reuse Hands thread state.

Implementation note (behavior-preserving): shared Mind provider helpers (schema path resolution, JSON extraction, JSONL transcript append, transcript filename stamping via `filename_safe_ts`) live under `mi/providers/mind_utils.py`.

Schema note (Codex `--output-schema`): Codex's `--output-schema` is effectively strict. For object schemas, every key in `properties` must appear in `required`. Optional fields must be expressed via `null` (e.g., `{"anyOf":[{"type":"null"},{...}]}`), not by omitting the key.

## Transparency

MI persists and exposes:

- Raw Hands transcript (full stdout/stderr stream, timestamped)
- EvidenceLog (JSONL): per-batch evidence, plus what MI sent to Hands and any repo observations

The user can choose to view:

- MI summaries only
- or expand to see raw transcript + evidence entries

Live run display (V1):

- `mi run` prints a live stream by default to reduce user burden and improve auditability:
  - `[mi]` MI stage/decision logs (high-level)
  - `[mi->hands]` the exact prompt MI sends to Hands (light injection + batch_input)
  - `[hands]` rendered Hands output (Codex JSON stream -> readable lines)
- Stored logs remain unchanged; `--redact` affects display only.
- Use `mi run --quiet` to suppress live output and the end summary.

## Soft-Constraint Violations (e.g., external actions without prior clarification)

Because MI does not intercept tools, "external actions" are a **soft policy** enforced by:

- Light injection guidance ("if not covered by values, pause and ask")
- Post-hoc detection from transcript/repo changes
- Automatic preference tightening (reversible; materialized as Thought DB preference Claims), plus optional immediate user escalation

Optional interrupt mode:

- MI may interrupt the Hands process when real-time transcript suggests a high-risk action is happening (implemented for Codex; other CLIs are best-effort).
- This behavior is controlled by runtime config (`config.runtime.interrupt`; default can be off).
- High-risk heuristic markers (best-effort): `git push`, `npm publish` / `twine upload`, `rm -rf` / `rm -r`, `sudo`, `curl|sh` / `wget|sh`.
- When `interrupt.mode=on_any_external`, MI may also interrupt for broader external markers (best-effort): installs (`pip install`, `npm install`, `pnpm install`, `yarn add`) and network fetches (`curl`, `wget`).

## Minimal Checks Policy (V1)

MI can propose or generate "minimal checks" when evidence is insufficient, prioritizing:

1) Existing project checks that Hands can run (tests/build/lint; default Hands: Codex)
2) Minimal new checks/tests (smoke test) **only if aligned with values/preferences**

Execution preference (aligned): **Hands runs checks** (MI only suggests/plans via next batch input).

Implementation: MI records a `check_plan` after each batch. To reduce latency/cost, MI may **skip** the `plan_min_checks` model call when evidence indicates no uncertainty/risk/questions; in that case it records a default `check_plan` with `should_run_checks=false` and a short note explaining the skip.

## Prompts (MI Prompt Pack)

MI uses the following internal prompts (all should return strict JSON):

1) `compile_values` (implemented; used by `mi init` / `mi values set`)
   - Input: user values/preferences prompt text (`values_text`)
   - Output: `compiled_values` with:
     - `values_summary` (concise)
     - `decision_procedure` (`summary` + Mermaid flowchart)
   - Notes:
     - `compiled_values` is stored for provenance + human inspection. Runtime decisions rely on canonical Thought DB claims/nodes.

2) `extract_evidence` (implemented)
   - Input: batch input MI sent to Hands, Hands provider hint (e.g., `codex|cli`), machine-extracted batch summary (incl. transcript event observation), and repo observation
   - Output: `EvidenceItem` with `facts`, `actions`, `results`, `unknowns`, `risk_signals`

3) `workflow_progress` (implemented; internal)
   - Input: workflow IR + current workflow cursor (`ProjectOverlay.workflow_run`) + latest evidence
   - Output: best-effort step completion + next step id (does not ask the user; does not enforce step-by-step reporting)

4) `risk_judge` (implemented; post-hoc)
   - Input: Hands provider hint + recent transcript snippets + runtime config + `EvidenceLog`
   - Output: risk judgement with `category`, `severity`, `should_ask_user`, `mitigation`, and optional preference tightening (`learn_suggested`)

5) `plan_min_checks` (implemented)
   - Input: Hands provider hint + runtime config, `ProjectOverlay`, repo observation, recent `EvidenceLog`
   - Output: a minimal check plan and (when needed) a single Hands instruction (`hands_check_input`) to execute the checks

6) `auto_answer_to_hands` (implemented)
   - Input: Hands provider hint + runtime config, `ProjectOverlay`, recent `EvidenceLog`, optional minimal check plan, and the raw Hands last message
   - Output: an optional Hands reply (`hands_answer_input`) that answers Hands' question(s) using values + evidence; only asks the user when MI cannot answer

7) `decide_next` (implemented)
   - Input: Hands provider hint + runtime config, `ProjectOverlay`, recent `EvidenceLog`, and a deterministic Thought DB context subgraph (`nodes` + `values_claims` + `pref_goal_claims` + `query_claims` + `edges`)
   - Output: `NextMove` (`send_to_hands | ask_user | stop`) plus `status` (`done|not_done|blocked`). This prompt also serves as MI's closure evaluation in the default loop. Note: pre-action arbitration may already have sent an auto-answer and/or minimal checks to Hands for that batch; in that case `decide_next` may be skipped for the iteration.

8) `loop_break` (implemented; internal)
   - Input: runtime config, `ProjectOverlay`, a Thought DB context, recent evidence, a loop pattern id + loop reason, Hands last message, and the planned next Hands instruction
   - Output: a best-effort action to break the loop (rewrite the next instruction, force checks, stop, or ask the user as a last resort).

9) `checkpoint_decide` (implemented; internal)
   - Input: runtime config, `ProjectOverlay`, a compact Thought DB context, and a compact "segment evidence" buffer + planned next Hands input (if any) + a status hint
   - Output: whether MI should cut a checkpoint boundary (segment) now, and whether it should mine workflows/preferences at this boundary.

10) `suggest_workflow` (implemented; optional)
   - Input: task + runtime config, `ProjectOverlay`, a compact Thought DB context, recent evidence (typically the current segment), and run notes
   - Output: either `should_suggest=false` or a suggested workflow IR + a stable `signature` (used to count occurrences across runs). MI may solidify it into stored workflows depending on `config.runtime.workflows` knobs.

11) `edit_workflow` (implemented; CLI)
   - Input: runtime config, `ProjectOverlay`, a compact Thought DB context, an existing workflow, and a natural-language edit request
   - Output: edited workflow IR + change_summary/conflicts/notes (used by `mi workflow edit`)

12) `mine_preferences` (implemented; optional)
   - Input: task + runtime config, `ProjectOverlay`, a compact Thought DB context, recent evidence (typically the current segment), and run notes
   - Output: a small list of suggested preference/goal guidance texts (scope=`project|global`) with confidence/benefit, suitable to store as Thought DB preference/goal Claims. MI uses occurrence counts to avoid noisy one-off learning.

13) `mine_claims` (implemented; optional; Thought DB)
   - Input: task + runtime config, `ProjectOverlay`, a compact Thought DB context, recent evidence (typically the current segment), and run notes
   - Output: a small list of atomic `Claim`s (fact/preference/assumption/goal) and optional edges. MI applies them into the append-only Thought DB (project/global) with provenance that cites **EvidenceLog `event_id` only** (high-threshold, best-effort).

14) `why_trace` (implemented; on-demand; Thought DB)
   - Input: a target (EvidenceLog `event_id` or a `claim_id`), an `as_of_ts`, and a bounded list of candidate claims (from recall/search).
   - Output: a minimal support set of `claim_id`s + short explanation + confidence. MI may materialize `depends_on(event_id -> claim_id)` edges when the target is an EvidenceLog `event_id`.

15) `values_claim_patch` (implemented; on-demand; values -> Thought DB)
   - Input: `values_text` + `compiled_values` + existing global values claims + allowed `event_id` list + allowed retract claim ids
   - Output: a small patch of global preference/goal Claims (plus optional supersedes/same_as edges) and a list of old claim_ids to retract. Used by `mi init` / `mi values set` (explicit user action) to keep values canonical as Thought DB claims (tagged `values:base`), citing a `values_set` event_id for provenance.

16) `learn_update` (implemented; optional; run-end)
   - Input: runtime config + `ProjectOverlay`, recent `learn_suggested` events (this run), and a compact list of existing learned claims + an allowed `event_id` list + an allowed retract list.
   - Output: a small Thought DB patch (claims+edges) plus optional retractions. MI applies it best-effort as append-only updates (citing **EvidenceLog `event_id` only**) and records `kind=learn_update`.

## Data Models (Minimal Schemas)

### RuntimeConfig + ProjectOverlay (prompt context; historical name: "MindSpec")

In V1 code and schemas, the runtime knobs object passed into Mind prompts is historically named `mindspec_base`.
It is **not** canonical for values/preferences (those live in Thought DB).

Prompt context is the merge of:

- `runtime` (from `<home>/config.json`): operational knobs/budgets/feature switches
- `project_overlay` (project-specific state under `projects/<project_id>/overlay.json`; may include derived mirrors of canonical preferences, e.g., a testless verification strategy)

Canonical values/preferences are stored in Thought DB as preference/goal Claims (see "Thought DB context" and "Thought DB (Claims + Nodes)").
Operational defaults (e.g., `ask_when_uncertain`, `refactor_intent`) are also stored canonically as Thought DB preference Claims tagged `mi:setting:*`.

Notes (V1):

- MI uses a `compiled_values` JSON shape (model output from `compile_values`) as an **intermediate** for `mi values set` / `mi init`.
- `values_text` is persisted canonically as a raw values preference Claim tagged `values:raw` (audit) and is also present in the global `values_set` evidence event payload.
- `values_summary` + `decision_procedure` are persisted canonically as a global Summary node tagged `values:summary` when compilation succeeds.
- These compiled fields are excluded from runtime Mind prompts (sanitized) so the model relies on Thought DB claims instead of duplicating/contradicting value text.

Runtime prompt hygiene (V1):

- Runtime Mind prompt-pack calls do not include raw values text, `values_summary`, or operational defaults. The model relies on canonical Thought DB context (Claims/Nodes) for values/preferences and defaults.

Minimal shape:

```json
{
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
  "workflows": {
    "auto_mine": true,
    "auto_enable": true,
    "min_occurrences": 2,
    "allow_single_if_high_benefit": true,
    "auto_sync_on_change": true
  },
  "cross_project_recall": {
    "enabled": true,
    "top_k": 3,
    "max_chars": 1800,
    "include_kinds": ["snapshot", "workflow", "claim", "node"],
    "exclude_current_project": false,
    "prefer_current_project": true,
    "triggers": {
      "run_start": true,
      "before_ask_user": true,
      "risk_signal": true
    }
  },
  "preference_mining": {
    "auto_mine": true,
    "min_occurrences": 2,
    "allow_single_if_high_benefit": true,
    "min_confidence": 0.75,
    "max_suggestions": 3
  },
  "violation_response": {
    "auto_learn": true,
    "ask_user_on_high_risk": true,
    "ask_user_risk_severities": ["high", "critical"],
    "ask_user_risk_categories": [],
    "ask_user_respect_should_ask_user": true,
    "learn_update": {
      "enabled": true,
      "min_new_suggestions_per_run": 2,
      "min_active_learned_claims": 3,
      "min_confidence": 0.9,
      "max_claims": 6,
      "max_retracts": 6
    }
  }
}
```

### ProjectOverlay

Note: `testless_verification_strategy` is a derived mirror/cache pointer to the canonical project-scoped Thought DB preference Claim tagged `mi:testless_verification_strategy`. The overlay stores only a `claim_id` pointer (not the full strategy text). MI may refresh/derive this pointer at run start (best-effort) when a canonical claim exists, so check planning can avoid re-asking for a one-time strategy.

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
    "claim_id": "cl_<id>",
    "rationale": "string"
  },
  "host_bindings": [
    {
      "host": "string",
      "workspace_root": "string",
      "enabled": true,
      "generated_rel_dir": "string",
      "register": {
        "symlink_dirs": [{"src": "string", "dst": "string"}]
      }
    }
  ],
  "hands_state": {
    "provider": "string",
    "thread_id": "string",
    "updated_ts": "string"
  },
  "global_workflow_overrides": {
    "wf_<id>": { "enabled": false }
  },
  "workflow_run": {
    "version": "v1",
    "active": true,
    "workflow_id": "string",
    "workflow_name": "string",
    "thread_id": "string",
    "started_ts": "RFC3339 timestamp",
    "updated_ts": "RFC3339 timestamp",
    "completed_step_ids": ["string"],
    "next_step_id": "string",
    "last_batch_id": "string",
    "last_confidence": 0.0,
    "last_notes": "string",
    "close_reason": "string"
  }
}
```

### Workflow IR (project + global)

MI can store reusable workflows as **project** or **global** JSON files. A workflow is MI-owned **source of truth**; host workspaces (e.g., OpenClaw) receive only derived artifacts via adapters.

Storage + precedence (V1):

- Project workflows: `<home>/projects/<project_id>/workflows/wf_*.json`
- Global workflows: `<home>/workflows/global/wf_*.json`
- Effective workflows for a project are a merge of (global + project), with **project always winning** when ids collide.
- A project may override a global workflow via `ProjectOverlay.global_workflow_overrides[workflow_id]`:
  - `enabled` (boolean): enable/disable the global workflow for this project
  - `step_patches` (dict): patch/disable individual steps by `step_id` (e.g., change `hands_input` for one step)
  - `steps_replace` (list): replace the entire `steps` list when structure/order changes are needed

Minimal shape:

```json
{
  "version": "v1",
  "id": "wf_<...>",
  "name": "string",
  "enabled": true,
  "trigger": {
    "mode": "manual | task_contains",
    "pattern": "string"
  },
  "mermaid": "string",
  "steps": [
    {
      "id": "string",
      "kind": "hands | check | gate",
      "title": "string",
      "hands_input": "string",
      "check_input": "string",
      "risk_category": "network | install | push | publish | delete | privilege | privacy | cost | other",
      "policy": "values_judged | allow | deny | ask",
      "notes": "string"
    }
  ],
  "source": {
    "kind": "manual | suggested",
    "reason": "string",
    "evidence_refs": ["string"]
  },
  "created_ts": "RFC3339 timestamp",
  "updated_ts": "RFC3339 timestamp"
}
```

### EvidenceLog (JSONL)

`evidence.jsonl` is append-only and may contain multiple record kinds:

- `hands_input` (exact MI input + light injection sent to Hands for the batch)
- `state_corrupt` (internal: an MI-owned JSON state file was unreadable/corrupt; MI quarantined it as `*.corrupt.<ts>` and continued with defaults; best-effort)
- `defaults_claim_sync` (internal: ensured operational defaults exist as canonical global Thought DB preference Claims tagged `mi:setting:*`; records the seed/sync outcome for audit)
- `evidence` (extracted summary per batch; includes a Mind transcript pointer for `extract_evidence`)
- `mind_error` (a Mind prompt-pack call failed; includes schema/tag + error + best-effort transcript pointer)
- `mind_circuit` (Mind circuit breaker state change; V1 emits `state="open"` when it stops attempting further Mind calls)
- `risk_event` (post-hoc judgement when heuristic risk signals are present; includes a Mind transcript pointer for `risk_judge`)
- `learn_suggested` (a suggested preference tightening produced by Mind; may be auto-applied as Thought DB preference Claims depending on `violation_response.auto_learn`)
- `learn_applied` (a manual application of a prior `learn_suggested` record; written by `mi claim apply-suggested ...`)
- `learn_update` (optional run-end consolidation of multiple `learn_suggested` items into a smaller canonical set; may write learned claims/edges and append-only retractions; best-effort)
- `testless_strategy_set` (internal: recorded a testless strategy set/update so a canonical preference Claim can cite an EvidenceLog `event_id`)
- `check_plan` (minimal checks proposed post-batch; includes a Mind transcript pointer for `plan_min_checks` when planned)
- `auto_answer` (MI-generated reply to Hands questions, when possible; includes a Mind transcript pointer for `auto_answer_to_hands`)
- `decide_next` (the per-batch decision output: done/not_done/blocked + next_action + notes; includes the raw `decide_next.json` object, a Mind transcript pointer, and a compact `thought_db` summary of claim/node ids used)
- `workflow_progress` (best-effort workflow cursor update from `workflow_progress`; helps MI infer completed/next steps without forcing step-by-step reporting)
- `checkpoint` (segment boundary judgement from `checkpoint_decide`; may trigger workflow/preference mining and segment reset)
- `snapshot` (a compact segment snapshot written at checkpoint boundaries; used for cross-project recall; includes `snapshot_id`; traceable via `source_refs` which may include `event_ids`)
- `node_materialized` (checkpoint materialization of Thought DB nodes (Decision/Action/Summary); lists written ids and traceability edges; best-effort)
- `cross_project_recall` (on-demand recall results for this run; includes `query_raw` + `query_compact` + `tokens_used`, plus a compact list of recalled items + traceable `source_refs`)
- `workflow_trigger` (an enabled workflow matched the user task and was injected into the first batch input)
- `workflow_suggestion` (output from `suggest_workflow` at a checkpoint/segment boundary; can occur multiple times per `mi run`)
- `workflow_solidified` (MI created a stored workflow IR from a repeated signature)
- `host_sync` (MI synced derived artifacts into bound host workspaces; includes sync results)
- `preference_mining` (output from `mine_preferences` at a checkpoint/segment boundary; can occur multiple times per `mi run`)
- `preference_solidified` (MI emitted a preference tightening suggestion (and may auto-apply as a Thought DB Claim) when a mined preference reaches its occurrence threshold)
- `claim_mining` (output from `mine_claims` at a checkpoint/segment boundary; includes applied Thought DB claim ids; best-effort, high-threshold)
- `claim_retract` (user-driven append-only claim retraction via CLI)
- `claim_supersede` (user-driven append-only claim update via CLI; implemented as new claim + supersedes edge)
- `claim_same_as` (user-driven append-only claim de-duplication via CLI; implemented as same_as edge)
- `settings_set` (user-driven operational settings change written by `mi settings set --scope project ...`; used for provenance of project-scoped setting claims)
- `node_create` (user-driven append-only Thought DB node creation via CLI; Decision/Action/Summary)
- `node_retract` (user-driven append-only Thought DB node retraction via CLI)
- `edge_create` (user-driven append-only Thought DB edge creation via CLI)
- `why_trace` (root-cause tracing output: minimal support set of claim ids + explanation; may materialize `depends_on(event_id -> claim_id)` edges; best-effort; on-demand via `mi why ...` and optionally auto at `mi run` end via `mi run --why` or `config.runtime.thought_db.why_trace.auto_on_run_end=true`)
- `loop_guard` (repeat-pattern detection for stuck loops)
- `loop_break` (Mind-guided loop breaking invoked after `loop_guard`; may rewrite the next instruction or force checks; best-effort)
- `user_input` (answers captured when MI asks the user)
- `hands_resume_failed` (best-effort: resume by stored thread/session id failed; MI fell back to a fresh exec)

Note: EvidenceLog is append-only and may include additional record kinds in newer versions.

Stable identifiers (V1+):

- `run_id`: unique per `mi run` invocation (or per CLI write session)
- `seq`: monotonically increasing within the `run_id`
- `event_id`: derived from `run_id` + `seq` (used for traceability; older logs may not include it)

```json
{
  "kind": "evidence",
  "event_id": "ev_<run_id>_<seq>",
  "run_id": "run_<...> | cli_<...>",
  "seq": 1,
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "hands_transcript_ref": "path",
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

`mind_circuit` record shape (when MI opens the circuit breaker after repeated failures):

```json
{
  "kind": "mind_circuit",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "state": "open",
  "threshold": 2,
  "failures_total": 0,
  "failures_consecutive": 0,
  "schema_filename": "string",
  "tag": "string",
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
    "hands_check_input": "string",
    "notes": "string"
  }
}
```

Note: MI may emit multiple `check_plan` records within a single batch cycle (e.g., `batch_id="b0"` then `batch_id="b0.after_testless"` or `batch_id="b0.after_tls_claim"`) when it re-plans after learning/deriving a one-time testless verification strategy (canonicalized as a Thought DB preference Claim tagged `mi:testless_verification_strategy`). If Thought DB already has a canonical strategy but `plan_min_checks` still requests it, MI re-plans once (`batch_id=...after_tls_claim`) and will not prompt the user.

Implementation note: MI uses a shared internal helper to resolve the testless strategy consistently across normal check planning and loop-break-triggered checks.

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

`auto_answer` record shape (MI reply suggestion to Hands):

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
    "hands_answer_input": "string",
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
  "next_action": "send_to_hands|ask_user|stop",
  "status": "done|not_done|blocked",
  "confidence": 0.0,
  "notes": "string",
  "ask_user_question": "string",
  "next_hands_input": "string",
  "mind_transcript_ref": "path",
  "thought_db": {
    "as_of_ts": "RFC3339 timestamp",
    "node_ids": ["string"],
    "values_claim_ids": ["string"],
    "pref_goal_claim_ids": ["string"],
    "query_claim_ids": ["string"],
    "edges_n": 0,
    "notes": "string"
  },
  "decision": {
    "...": "raw decide_next.json object"
  }
}
```

`why_trace` record shape (root-cause tracing output; on-demand and optional run-end auto):

```json
{
  "kind": "why_trace",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "target": {
    "target_type": "evidence_event|claim",
    "...": "target metadata"
  },
  "as_of_ts": "RFC3339 timestamp",
  "query": "string",
  "candidate_claim_ids": ["string"],
  "state": "ok|error|skipped",
  "mind_transcript_ref": "path",
  "output": {
    "...": "raw why_trace.json object"
  },
  "written_edge_ids": ["string"]
}
```

`workflow_progress` record shape (best-effort workflow cursor update):

```json
{
  "kind": "workflow_progress",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "workflow_id": "string",
  "workflow_name": "string",
  "state": "ok|error|skipped",
  "mind_transcript_ref": "path",
  "output": {
    "should_update": true,
    "completed_step_ids": ["string"],
    "next_step_id": "string",
    "should_close": false,
    "close_reason": "string",
    "confidence": 0.0,
    "notes": "string"
  }
}
```

`checkpoint` record shape (segment checkpoint judgement):

```json
{
  "kind": "checkpoint",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "segment_id": "string",
  "state": "ok|error|skipped",
  "mind_transcript_ref": "path",
  "planned_next_input": "string",
  "status_hint": "string",
  "note": "string",
  "output": {
    "should_checkpoint": true,
    "checkpoint_kind": "none|phase_change|subtask_complete|risk_boundary|user_interaction|timebox|max_batches|done|blocked|other",
    "should_mine_workflow": false,
    "should_mine_preferences": false,
    "confidence": 0.0,
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
  "hands_last_message": "string",
  "next_input": "string",
  "reason": "string"
}
```

`loop_break` record shape (best-effort loop breaking invoked after `loop_guard`):

```json
{
  "kind": "loop_break",
  "batch_id": "string",
  "ts": "RFC3339 timestamp",
  "thread_id": "string",
  "pattern": "aaa|abab",
  "reason": "string",
  "state": "ok|error|skipped",
  "mind_transcript_ref": "path",
  "output": {
    "action": "rewrite_next_input|run_checks_then_continue|stop_done|stop_blocked|ask_user",
    "confidence": 0.0,
    "rewritten_next_input": "string",
    "check_intent": "string",
    "ask_user_question": "string",
    "notes": "string"
  }
}
```

### Risk (post-hoc judgement)

```json
{
  "category": "network|install|push|publish|delete|privilege|privacy|cost|other",
  "severity": "low|medium|high|critical",
  "should_ask_user": true,
  "mitigation": ["string"],
  "learn_suggested": []
}
```

If a `risk_event` is detected, MI may immediately prompt the user to continue depending on `config.runtime.violation_response` knobs:

- `ask_user_on_high_risk` (master switch)
- `ask_user_risk_severities` (which severities to prompt for)
- `ask_user_risk_categories` (optional allow-list; empty means any)
- `ask_user_respect_should_ask_user` (when true, prompt only if `risk_judge.should_ask_user=true`)

### Preference Tightening (`learn_suggested`) (append-only, reversible)

Mind prompts may output `learn_suggested` suggestions from:

- `risk_judge.learn_suggested`
- `decide_next.learn_suggested`

V1 strict Thought DB mode treats these as **preference tightening suggestions** and stores them canonically as Thought DB preference Claims (append-only, reversible).

`config.runtime.violation_response.auto_learn` controls what MI does:

- If `auto_learn=true` (default): MI materializes each suggestion as a Thought DB `Claim` (`claim_type=preference`, scope=`project|global`) and records a `learn_suggested` EvidenceLog record with `applied_claim_ids`.
- If `auto_learn=false`: MI does **not** write claims automatically; it records `learn_suggested` into EvidenceLog for audit and you can apply it later via CLI (`mi claim apply-suggested ...`), which appends the preference Claims and records `learn_applied`.

Rollback:

- Retract an auto-learned preference Claim via `mi claim retract <claim_id> ...` (append-only).

Optional consolidation (`learn_update`) (run-end):

- When `config.runtime.violation_response.learn_update.enabled=true` and `violation_response.auto_learn=true`, MI may run **at most one** additional Mind call at `mi run` end to consolidate the run's `learn_suggested` noise into a small canonical Thought DB patch.
- Gated conservatively by thresholds such as:
  - `min_new_suggestions_per_run` (default 2)
  - `min_active_learned_claims` (default 3; project scope)
  - `min_confidence` / `max_claims` / `max_retracts`
- The model may emit:
  - new learned preference/goal Claims (tagged `mi:learned`)
  - evolution edges (`supersedes` / `same_as`) between claims
  - optional append-only retractions of prior learned claims (allowlisted)
- Provenance: every new claim/edge/retraction must cite **EvidenceLog `event_id` only** from the allowed list (the `learn_suggested` events produced in the same run).
- MI records `kind=learn_update` with the raw output and an `applied` summary (written claims/edges + retractions).

## Workflows + Host Adapters (V1, Experimental)

MI may "solidify" a user's habits into reusable workflows:

- Workflows can be **project-scoped** or **global** in V1 (project always wins when ids collide).
- Workflow IR is stored in MI home as the source of truth:
  - project: `projects/<project_id>/workflows/*.json`
  - global: `workflows/global/*.json`
- A project may override a global workflow's enabled flag via `ProjectOverlay.global_workflow_overrides[workflow_id].enabled`.
- Host workspace artifacts (e.g., Skills) are **derived** outputs written into a host workspace under an MI-owned generated directory (by default: `./.mi/generated/<host>/...`), and can be regenerated at any time.

Workflow mining/solidification policy is values-driven, but MI exposes coarse knobs in `config.runtime.workflows`:

- `auto_mine`: allow MI to call `suggest_workflow` and record candidates.
- `min_occurrences`: usually require >=2 similar occurrences before writing a stored workflow.
- `allow_single_if_high_benefit`: allow 1-shot solidification when benefit is extremely high.
- `auto_enable`: when solidified, whether workflows default to enabled.
- `auto_sync_on_change`: when workflows change (create/edit/enable/disable), sync derived artifacts to bound host workspaces.

Workflow behavior in `mi run` (V1):

- Trigger routing: if an **enabled effective** workflow has `trigger.mode=task_contains` and its `pattern` matches the user task, MI injects the workflow into the **first** Hands batch input (lightweight; no step slicing). MI records `kind=workflow_trigger`.
- Step cursor (best-effort): when a workflow is active, MI maintains `ProjectOverlay.workflow_run` and updates it via `workflow_progress` after each Hands batch. This is used as context for `decide_next` but does not force Hands into step-by-step reporting.
- Auto mining (checkpoint-based): if `config.runtime.workflows.auto_mine=true`, MI may call `suggest_workflow` at LLM-judged checkpoints (segment boundaries) during `mi run` (including at run end) and records `kind=workflow_suggestion`.
  - MI increments the occurrence count for `suggestion.signature` in `projects/<project_id>/workflow_candidates.json` (at most once per `mi run` invocation per signature).
  - When the occurrence threshold is met (usually `min_occurrences`, or 1-shot when `benefit=high` and `allow_single_if_high_benefit=true`), MI writes a stored **project** workflow JSON under `projects/<project_id>/workflows/` and records `kind=workflow_solidified` (V1 solidification is project-scoped by default).
  - If `auto_sync_on_change=true`, MI then syncs derived artifacts into all bound host workspaces and records `kind=host_sync`.

OpenClaw adapter (Skills-only):

- The OpenClaw integration target is the *Skills* mechanism (AgentSkills-compatible `SKILL.md` skill folders).
- MI generates skill folders under `./.mi/generated/openclaw/skills/<skill_dir>/SKILL.md` (plus `workflow.json` for audit).
- MI registers each generated skill dir into the host workspace as a symlink at `./skills/<skill_dir>` (best-effort, reversible; tracked via `manifest.json` under the generated root).
- Host adapters are implemented as a small registry (host name -> adapter) so MI storage/IR remains host-decoupled; host workspaces receive derived artifacts only.

## Preference Mining (V1, Experimental)

MI may mine likely-stable user preferences/habits from MI-captured transcript/evidence and emit preference tightening suggestions (canonicalized as Thought DB preference Claims when applied).

Knobs in `config.runtime.preference_mining`:

- `auto_mine`: allow MI to call `mine_preferences` at LLM-judged checkpoints during `mi run` (including at run end).
- `min_occurrences`: usually require >=2 similar occurrences before emitting a suggestion.
- `allow_single_if_high_benefit`: allow 1-shot emission when benefit is extremely high.
- `min_confidence`: skip suggestions below this confidence to reduce noisy learning.
- `max_suggestions`: cap the number of suggestions considered per `mi run`.

Behavior in `mi run` (V1):

- MI may call `mine_preferences` at checkpoints (segment boundaries) during `mi run` and records `kind=preference_mining`.
- MI computes a stable signature from `(scope + normalized suggestion text)` and increments the occurrence count in `projects/<project_id>/preference_candidates.json` (at most once per `mi run` invocation per signature).
- When the occurrence threshold is met, MI emits a `kind=learn_suggested` record (source=`mine_preferences`) and records `kind=preference_solidified`.
  - If `violation_response.auto_learn=true`, MI also appends the preference tightening as a Thought DB preference Claim and includes `applied_claim_ids` in the `learn_suggested` record.

## Thought DB (Claims + Nodes) (V1, Experimental)

MI can maintain a durable, provenance-traceable "Thought DB" of atomic reusable `Claim`s (the "basic arguments") that support future root-cause tracing ("why did we do this?").

V1 scope (implemented):

- Append-only Claim + Edge stores (project + global)
- Append-only Node store (project + global) for `Decision` / `Action` / `Summary` nodes
- `source_refs` cite **EvidenceLog `event_id` only** (no direct references to external logs)
- Values in Thought DB (canonical):
  - Global EvidenceLog event `kind=values_set` (provenance anchor)
  - Raw values preference Claim tagged `values:raw` (audit; excluded from runtime injection/recall)
  - Derived preference/goal Claims tagged `values:base` (runtime; injected into Hands)
  - Global Summary node tagged `values:summary` (human-facing; optional; append-only)
- Checkpoint-only, high-threshold claim mining during `mi run` (no user prompts)
- Deterministic checkpoint materialization of `Decision` / `Action` / `Summary` nodes during `mi run` (no extra model calls; best-effort; append-only)
- Basic CLI management via `mi claim ...`, `mi node ...`, and `mi edge ...`

Knobs in `config.runtime.thought_db`:

- `enabled`: enable Thought DB features (default true)
- `auto_mine`: allow MI to call `mine_claims` at LLM-judged checkpoints during `mi run` (default true)
- `auto_materialize_nodes`: create `Decision` / `Action` / `Summary` nodes at checkpoint boundaries (deterministic; no extra model calls) (default true)
- `min_confidence`: skip claims below this confidence (default 0.9)
- `max_claims_per_checkpoint`: cap the number of claims written per checkpoint (default 6)
- `why_trace` (optional run-end explainability; best-effort; one call per `mi run`):
  - `auto_on_run_end`: when true, run one `why_trace` at `mi run` end (default false)
  - `top_k`: number of candidate claims to consider (default 12)
  - `min_write_confidence`: minimum confidence required to materialize `depends_on(event_id -> claim_id)` edges (default 0.7)
  - `write_edges`: when true, allow materializing `depends_on` edges from the target event to chosen claims (default true)

Behavior in `mi run` (V1):

- At checkpoint boundaries, MI may call `mine_claims` and records `kind=claim_mining`.
- Only "high-confidence, reusable" claims (and optional edges) should be written; otherwise MI writes none (to avoid noisy graphs).
- At checkpoint boundaries, MI may also materialize Thought DB nodes (Decision/Action/Summary) derived from the segment evidence + snapshot + decide_next; it records `kind=node_materialized` (best-effort) and may add `derived_from(node_id -> event_id)` edges for traceability.

Notes:

- Root-cause tracing is implemented via `mi why ...` (WhyTrace) and may materialize `depends_on(event_id -> claim_id)` edges (best-effort). Optional: `mi run --why` (or `config.runtime.thought_db.why_trace.auto_on_run_end=true`) runs one WhyTrace at run end for auditability. Bounded subgraph inspection is available via `mi claim show --graph` / `mi node show --graph` (JSON-only; best-effort). Whole-graph refactors remain future work; see `docs/mi-thought-db.md`.
- Claims are optionally indexed into the memory text index as `kind=claim` (active, canonical only).
- Performance note: within a single `mi run`, MI keeps a hot in-memory Thought DB view and incrementally updates it after append-only writes (claims/nodes/edges). To keep cold-start fast across runs, MI also flushes `view.snapshot.json` at run end (best-effort).

## Storage Layout (V1)

Default MI home: `~/.mind-incarnation` (override with `$MI_HOME` or `mi --home ...`).

- Timestamp placeholders (`<ts>`) use filename-safe RFC3339 stamps (see `mi/core/storage.py` `filename_safe_ts`).

- Global:
  - `config.json` (Mind/Hands providers + runtime knobs)
  - `backups/config.json.<ts>.bak` + `backups/config.last_backup` (created by `mi config apply-template`; rollback uses the marker)
  - `global/evidence.jsonl` (global EvidenceLog for values + operational defaults lifecycle; provides stable `event_id` provenance for global preference/goal Claims)
  - `global/project_selection.json` (non-canonical convenience: `@last/@pinned/@alias` project root selection for "run from anywhere")
  - `global/transcripts/mind/*.jsonl` (optional; used for Mind calls outside a project, e.g., `mi values set`)
  - `thoughtdb/global/claims.jsonl` (global Claims)
  - `thoughtdb/global/edges.jsonl` (global Edges)
  - `thoughtdb/global/nodes.jsonl` (global Nodes)
  - `thoughtdb/global/view.snapshot.json` (optional; persisted materialized view for faster cold loads; safe to delete)
  - `thoughtdb/global/archive/<ts>/*.jsonl.gz` + `thoughtdb/global/archive/<ts>/manifest.json` (optional; created by `mi gc thoughtdb --global`)
- Per project (keyed by a resolved `project_id`):
  - `projects/<project_id>/overlay.json`
  - `projects/<project_id>/evidence.jsonl`
  - `projects/<project_id>/segment_state.json` (best-effort segment buffer for checkpoint-based mining; internal)
  - `projects/<project_id>/thoughtdb/claims.jsonl` (project Claims)
  - `projects/<project_id>/thoughtdb/edges.jsonl` (project Edges)
  - `projects/<project_id>/thoughtdb/nodes.jsonl` (project Nodes)
  - `projects/<project_id>/thoughtdb/view.snapshot.json` (optional; persisted materialized view for faster cold loads; safe to delete)
  - `projects/<project_id>/thoughtdb/archive/<ts>/*.jsonl.gz` + `projects/<project_id>/thoughtdb/archive/<ts>/manifest.json` (optional; created by `mi gc thoughtdb`)
  - `projects/<project_id>/workflows/*.json` (workflow IR; source of truth)
  - `projects/<project_id>/workflow_candidates.json` (signature -> count; used for workflow mining)
  - `projects/<project_id>/preference_candidates.json` (signature -> count; used for preference mining)
  - `projects/<project_id>/transcripts/hands/*.jsonl`
  - `projects/<project_id>/transcripts/hands/archive/*.jsonl.gz` (optional; created by `mi gc transcripts`)
  - `projects/<project_id>/transcripts/mind/*.jsonl`
  - `projects/<project_id>/transcripts/mind/archive/*.jsonl.gz` (optional; created by `mi gc transcripts`)

Note: `project_id` is derived deterministically from `identity_key`:

- `identity_key`: produced by `project_identity()`:
  - git repos: normalized remote origin (or best-effort fallback) + relpath within the repo
  - non-git dirs: resolved absolute path
- `project_id = sha256(identity_key)[:16]`

This is stable across path moves/clones for git repos (and supports monorepo subprojects via relpath).

Transcript archiving (optional): `mi gc transcripts` can gzip older transcripts into `archive/` and replace the original `.jsonl` with a small JSONL stub record:

```json
{"type":"mi.transcript.archived","archived_path":".../archive/<name>.jsonl.gz", "...":"..."}
```

Thought DB compaction (optional): `mi gc thoughtdb` archives Thought DB JSONL files into `thoughtdb/archive/<ts>/` as `.gz`, then rewrites compacted JSONL files (still append-only from that point onward). It also deletes `view.snapshot.json` and rebuilds it on the next load.

Crash-safe state (V1): MI writes MI-owned JSON state files using atomic replace (to avoid partial writes). If an MI-owned state file is unreadable/corrupt (e.g., JSON parse error), MI quarantines it as `*.corrupt.<ts>` and continues with defaults (best-effort). `mi run` records a `kind=state_corrupt` EvidenceLog record when this happens. By default, low-level state reads only print to stderr when no warning collector is used; you can force printing with `$MI_STATE_WARNINGS_STDERR=1` or force silence with `$MI_STATE_WARNINGS_STDERR=0`.

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
- Validate: `mi config validate`
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

MI wraps an agent CLI by capturing stdout/stderr into an MI-owned transcript. Claude Code flags may vary by version, but the config below is intended to be runnable on a typical install.

Edit `<home>/config.json`:

```json
{
  "hands": {
    "provider": "cli",
    "cli": {
      "prompt_mode": "arg",
      "exec": ["claude", "-p", "{prompt}", "--output-format", "stream-json"],
      "resume": ["claude", "-p", "{prompt}", "--output-format", "stream-json", "--resume", "{thread_id}"],
      "thread_id_regex": "\"(?:session_id|sessionId)\"\\s*:\\s*\"([A-Za-z0-9_-]+)\"",
      "env": {}
    }
  }
}
```

Notes:

- If your Claude Code install requires env, set it in your shell (preferred) or under `hands.cli.env`.
- Use `-p` ("print") to run headless/non-interactive.
- If your CLI can output JSON (e.g., `--output-format stream-json` or `json`), MI will parse it (best-effort) to improve evidence extraction, last-message detection, and session id extraction.
- `thread_id_regex` is a fallback only: it extracts an id from raw text if no JSON session id is available.
- If you want MI to resume the last Claude Code session across separate `mi run` invocations, also set `hands.continue_across_runs=true`.

Set values (canonical: Thought DB):

```bash
mi --home ~/.mind-incarnation values set --text "..."
mi --home ~/.mind-incarnation init --values "..."  # shortcut
mi --home ~/.mind-incarnation values show
```

Common value flags (`mi init` / `mi values set`):

- `--show`: print `values_summary` and `decision_procedure` after compiling
- `--dry-run`: compile and print, but do not write Thought DB
- `--no-compile`: skip model compilation and record `values_set` + raw values only (no derived values claims)
- `--no-values-claims`: skip migrating values into global Thought DB preference/goal Claims

Notes:

- `mi init` / `mi values set` appends a global EvidenceLog `values_set` event under `global/evidence.jsonl` so global value claims can cite stable `event_id` provenance.
- It also writes a raw values preference Claim tagged `values:raw` (audit). When compilation succeeds (i.e., not `--no-compile`), it also writes a global Summary node tagged `values:summary` (human-facing).
- Unless `--no-compile` or `--no-values-claims` is set, it calls `values_claim_patch` and applies it into `thoughtdb/global/*` as preference/goal Claims tagged `values:base`.
- `mi run` may append a global EvidenceLog `mi_defaults_set` event under `global/evidence.jsonl` when seeding missing operational defaults into canonical global Thought DB preference Claims tagged `mi:setting:*` (it does not continuously sync from runtime config defaults).
- Use `mi settings ...` to inspect/update operational settings:

```bash
mi --home ~/.mind-incarnation settings show --cd <project_root>
mi --home ~/.mind-incarnation settings set --ask-when-uncertain ask --refactor-intent behavior_preserving
mi --home ~/.mind-incarnation settings set --scope project --cd <project_root> --ask-when-uncertain proceed
```

Run batch autopilot:

```bash
# <task> can be multi-word without quotes (quotes still work):
mi --home ~/.mind-incarnation run --cd <project_root> <task words...>

# Script/CI:
mi --home ~/.mind-incarnation run --cd <project_root> --quiet <task words...>

# Debug:
mi --home ~/.mind-incarnation run --cd <project_root> --hands-raw <task words...>
```

Everyday status (front-door, read-only):

```bash
mi --home ~/.mind-incarnation status --cd <project_root>
mi --home ~/.mind-incarnation status --cd <project_root> --json
mi --home ~/.mind-incarnation --here status --json
```

Notes:

- `mi status` is read-only: it does not update `@last` / `global/project_selection.json`.
- It aggregates: config/provider health, canonical values readiness, latest batch bundle summary, pending learn suggestions, and next-step command hints.

Show an MI resource (front-door):

```bash
# Show by id:
mi --home ~/.mind-incarnation show ev_<id> --cd <project_root>
mi --home ~/.mind-incarnation show cl_<id> --cd <project_root>
mi --home ~/.mind-incarnation show nd_<id> --cd <project_root>
mi --home ~/.mind-incarnation show wf_<id> --cd <project_root>
mi --home ~/.mind-incarnation show ed_<id> --cd <project_root>

# EvidenceLog: search global only (skip project fallback):
mi --home ~/.mind-incarnation show ev_<id> --global

# Transcript path tail:
mi --home ~/.mind-incarnation show /path/to/transcript.jsonl -n 200

# Convenience pseudo-refs:
mi --home ~/.mind-incarnation show last --cd <project_root>
mi --home ~/.mind-incarnation show hands --cd <project_root> -n 200
mi --home ~/.mind-incarnation show mind --cd <project_root> -n 200
mi --home ~/.mind-incarnation show project --cd <project_root>

# Tail recent activity:
mi --home ~/.mind-incarnation tail --cd <project_root> -n 20
mi --home ~/.mind-incarnation tail evidence --cd <project_root> -n 20 --raw
mi --home ~/.mind-incarnation tail evidence --global -n 20 --json
mi --home ~/.mind-incarnation tail hands --cd <project_root> -n 200
mi --home ~/.mind-incarnation tail mind --cd <project_root> -n 200 --jsonl
```

Notes:

- `mi show ev_...` searches the project EvidenceLog first, then falls back to the global EvidenceLog.
- `mi show ev_... --global` searches the global EvidenceLog only.
- `mi show cl_/nd_/ed_/wf_...` uses effective resolution (project first, then global).
- `mi show <path>.jsonl` prints a transcript tail (best-effort; supports archive stubs and `.gz`).
- `mi show last/project/hands/mind` are pseudo-refs routed by the front-door show handler.
- `mi tail [evidence|hands|mind]` is the canonical tail entry:
  - default target is `evidence`
  - evidence default lines is `20`; transcript default lines is `200`
  - `mi tail evidence --raw` prints raw JSONL lines
  - `mi tail evidence --json` prints parsed JSON records
  - `mi tail evidence --global` tails global EvidenceLog only
  - `mi tail hands|mind --jsonl` prints raw transcript JSONL lines

List resources:

```bash
mi --home ~/.mind-incarnation claim list --cd <project_root>
mi --home ~/.mind-incarnation node list --cd <project_root>
mi --home ~/.mind-incarnation workflow list --cd <project_root>
mi --home ~/.mind-incarnation edge list --cd <project_root>
```

Edit a workflow (uses Mind provider):

```bash
mi --home ~/.mind-incarnation workflow edit wf_<id> --cd <project_root> --request "..."
mi --home ~/.mind-incarnation workflow edit wf_<id> --cd <project_root> --loop
```

Notes on `--cd` (project root):

- Most project-scoped commands accept `--cd <project_root>` to choose which project to operate on.
- You can also set a per-invocation default project root via `mi -C <project_root> <cmd> ...` (argparse: `-C/--cd` must appear **before** the subcommand). Subcommand `--cd` overrides `-C/--cd` if both are provided.
- You can force the project root to be the current working directory (even inside a git repo) via `mi --here <cmd> ...` (global flag; must appear **before** the subcommand). This is useful for monorepo subdirs and is ignored when `--cd/-C` is provided.
- `--cd` is optional. If omitted, MI infers a project root from your current working directory:
  - for git repos: defaults to the git toplevel (repo root) unless the current directory was previously used as a distinct MI project root (monorepo subproject)
  - for non-git dirs: uses `@pinned` (if recorded), otherwise `@last` (if recorded), otherwise uses the current directory
- You can also set `$MI_CD` (a path or `@last/@pinned/@alias`) to run MI commands from anywhere without repeating `--cd`/`-C`.
- `--cd` also supports selection tokens:
  - `--cd @last` (last used project)
  - `--cd @pinned` (pinned project)
  - `--cd @<alias>` (user-defined alias)
  - Manage them via: `mi project use`, `mi project pin/unpin`, `mi project alias add/rm/list`
- `config.runtime.project_selection.auto_update_last` controls whether project-scoped commands update `@last` automatically (default: true).

Common run flags:

- `--max-batches N`: cap the number of Hands batches
- `--continue-hands`: try to resume the last stored Hands thread/session id for this project (best-effort)
- `--reset-hands`: clear the stored Hands thread/session id for this project before running
- `--quiet`: suppress live output and the end summary (scripts/CI)
- `--hands-raw`: print raw Hands stdout/stderr capture (Codex: JSON event lines) instead of rendered output
- `--no-mi-prompt`: do not print the full MI->Hands prompt (still persisted to EvidenceLog)
- `--redact`: best-effort redact common secret/token patterns in live display output (stored logs unchanged)
- `--why`: opt-in: run one WhyTrace at run end (writes `kind=why_trace`; may materialize `depends_on(event_id -> claim_id)` edges)

Inspect latest batch bundle (MI input + last agent message + evidence pointers + mind transcript pointers):

```bash
mi --home ~/.mind-incarnation show last --cd <project_root>
mi --home ~/.mind-incarnation show last --cd <project_root> --json
mi --home ~/.mind-incarnation show last --cd <project_root> --redact
```

Note: `mi show last` includes any `learn_update` / `learn_suggested` / `learn_applied` records related to the latest batch cycle, so you can quickly apply pending suggestions via `mi claim apply-suggested ...`. When MI records WhyTrace for the latest batch cycle (e.g., via `mi run --why` or `config.runtime.thought_db.why_trace.auto_on_run_end=true`), `mi show last --json` also includes `why_trace` and `why_traces`. `mi show last --json` also includes `state_corrupt_recent` (a pointer to the most recent `kind=state_corrupt` record) for on-demand diagnosis. When MI detects a stuck repetition loop, `mi show last --json` also includes `loop_guard` and `loop_break`.

Inspect per-project state (overlay + resolved paths):

```bash
mi --home ~/.mind-incarnation project show --cd <project_root>
mi --home ~/.mind-incarnation project show --cd <project_root> --json
mi --home ~/.mind-incarnation project show --cd <project_root> --redact
```

Show how MI resolves the project root (read-only; does not update `@last`):

```bash
mi --home ~/.mind-incarnation project status
mi --home ~/.mind-incarnation project status --json
mi --home ~/.mind-incarnation --here project status --json
```

Project selection shortcuts (`@last/@pinned/@alias`):

```bash
mi --home ~/.mind-incarnation project use --cd <project_root>         # set @last
mi --home ~/.mind-incarnation project pin --cd <project_root>         # set @pinned
mi --home ~/.mind-incarnation project unpin                           # clear @pinned
mi --home ~/.mind-incarnation project alias add repo1 --cd <project_root>
mi --home ~/.mind-incarnation project alias list

mi --home ~/.mind-incarnation run --cd @repo1 "<task>"
mi --home ~/.mind-incarnation run --cd @pinned "<task>"
```

Tail EvidenceLog:

```bash
mi --home ~/.mind-incarnation tail --cd <project_root> -n 20
mi --home ~/.mind-incarnation tail evidence --cd <project_root> -n 20 --raw
mi --home ~/.mind-incarnation tail evidence --cd <project_root> -n 20 --raw --redact
mi --home ~/.mind-incarnation tail evidence --global -n 20 --json
```

Show an EvidenceLog record by `event_id`:

```bash
mi --home ~/.mind-incarnation show <event_id> --cd <project_root>
mi --home ~/.mind-incarnation show <event_id> --cd <project_root> --redact
mi --home ~/.mind-incarnation show <event_id> --global
```

Memory index (for cross-project recall; materialized view):

```bash
mi --home ~/.mind-incarnation memory index status
mi --home ~/.mind-incarnation memory index rebuild
mi --home ~/.mind-incarnation memory index rebuild --no-snapshots
```

Notes:

- Rebuild deletes and recreates `<home>/indexes/memory.sqlite` from MI stores and EvidenceLog `snapshot` records (safe; derived).
- Recall is text-only in V1: it searches indexed items by kind and compacts queries into safe tokens (no embeddings). Default `cross_project_recall.include_kinds` is conservative and Thought-DB-first: `snapshot` / `workflow` / `claim` / `node`. EvidenceLog `kind=cross_project_recall` records `query_raw` + `query_compact` + `tokens_used`. Node items are indexed incrementally when MI creates them (checkpoint materialization) and are backfilled on `mi memory index rebuild`. When `cross_project_recall.prefer_current_project=true` (default) and `exclude_current_project=false`, results are re-ranked to prefer the current project first, then global, then other projects.
- Memory backend is pluggable (internal): default is `sqlite_fts` (persisted at `<home>/indexes/memory.sqlite`). You can override via `$MI_MEMORY_BACKEND` (e.g., `in_memory` for ephemeral/test runs). `mi memory index status` prints the active backend.
- Thought DB direction: V1 includes append-only Claim/Edge stores + checkpoint-only claim mining; full root-cause tracing and whole-graph refactors remain future extensions. See `docs/mi-thought-db.md`.
- Internal implementation note: orchestration helpers are modularized under `mi/runtime/autopilot/` (including an explicit state-machine/contracts layer in `state_machine.py` + `contracts.py`, workflow cursor helpers, batch context/effects helpers with shared context construction in `batch_context.py` (`build_batch_execution_context`), pre-decide pipeline helpers, reusable phase helpers for evidence/risk policy, orchestration service hooks under `mi/runtime/autopilot/services/` including pipeline/decide/checkpoint wrappers, and run-end flows for checkpoint/learn_update/WhyTrace). Run-level wiring is centralized through `RunSession` (`run_context.py`) + `RunLoopOrchestrator` (`orchestrator.py`) so `run_autopilot` remains a thin coordinator. Segment/checkpoint state IO + compacting are isolated in `segment_state.py`; checkpoint decision/orchestration + mining + deterministic node materialization are isolated in `checkpoint_pipeline.py`, `checkpoint_mining.py`, and `node_materialize.py`; evidence-event append/window/segment side-effect helpers are isolated in `evidence_flow.py`; evidence window append/trim helper is isolated in `batch_effects.py`; user-input/auto-answer record write helpers are isolated in `interaction_record_flow.py`; claim-mining helpers are isolated in `claim_mining_flow.py`; check-plan query/record helpers are isolated in `check_plan_flow.py`; canonical testless strategy sync/write and TLS resolution/replan helpers are isolated in `testless_strategy_flow.py`; ask-user branch orchestration + re-decide-after-user helpers are isolated in `ask_user_flow.py`; pre-decide user-interaction/retry helpers are isolated in `predecide_user_flow.py`; decide-next prompt query/record side-effect helpers are isolated in `decide_query_flow.py`; mind-call/circuit-break helper is isolated in `mind_call_flow.py`; cross-project recall write-through helper is isolated in `recall_flow.py`; risk pre-decide orchestration, risk-event append/window/segment side-effect helpers, and decide-next routing/missing-action helpers are isolated in `risk_predecide.py`, `risk_event_flow.py`, and `decide_actions.py`; loop-guard/loop-break + next-input queue helpers are isolated in `next_input_flow.py`; loop-break checks input helper is isolated in `loop_break_checks_flow.py`; workflow-progress latest-evidence/query/event/persist helpers are isolated in `workflow_progress_flow.py`; auto-answer query/fallback normalization is isolated in `auto_answer_flow.py`; learn-suggested normalization/application is isolated in `learn_suggested_flow.py` (behavior-preserving). Runner-local helper wiring is additionally centralized via `_mk_*_deps` constructors to reduce repeated dependency assembly and branch drift. Each batch still runs through explicit pre-decide sub-phases (`run_hands` + preaction arbitration helper around checks/auto-answer) and then falls through to a dedicated `decide_next` phase helper when needed; the decide phase further isolates `decide_next`-missing fallback and `next_action=ask_user` handling into focused helpers, with preserved behavior. CLI handling is split between `mi/cli_dispatch.py` and `mi/cli_commands/` (including `show`/`tail`, domain handlers, runtime command handlers for `run`/`memory`/`gc`, status/project-selection handlers, and config/init/values/settings handlers). Values writing logic is further extracted into `mi/cli_commands/values_set_flow.py` (`run_values_set_flow`) and injected by `cli_dispatch` to keep value compilation/writes reusable and testable with unchanged behavior. Thought DB storage is layered behind `ThoughtDbStore` via append/view/service components (`mi/thoughtdb/append_store.py`, `mi/thoughtdb/view_store.py`, `mi/thoughtdb/service_store.py`) and a shared application facade `mi/thoughtdb/app_service.py` (used by runner, `show`/`workflow`/`claim`/`node`/`why` commands, and run-end WhyTrace candidate flow for effective lookup/subgraph/decide-context/why-candidate assembly), with unchanged external behavior and storage contracts. Provider wiring is modularized via `mi/providers/mind_registry.py` + `mi/providers/hands_registry.py` (re-exported by `mi/providers/provider_factory.py`). Host adapters (derived artifacts + best-effort registration) are modularized under `mi/workflows/host_adapters/` (registry: `mi/workflows/host_adapters/registry.py`) and orchestrated via `mi/workflows/hosts.py` (behavior-preserving).

Show raw transcript (defaults to latest Hands transcript; Mind transcripts optional):

```bash
mi --home ~/.mind-incarnation tail hands --cd <project_root> -n 200
mi --home ~/.mind-incarnation tail mind --cd <project_root> -n 200
mi --home ~/.mind-incarnation tail hands --cd <project_root> -n 200 --jsonl --redact
```

Optional: archive older transcripts (gzip + stubs; default is dry-run):

```bash
mi --home ~/.mind-incarnation gc transcripts --cd <project_root>
mi --home ~/.mind-incarnation gc transcripts --cd <project_root> --apply
```

Apply a recorded suggestion as Thought DB preference Claims (when `violation_response.auto_learn=false` or if you want manual control):

```bash
mi --home ~/.mind-incarnation claim apply-suggested <suggestion_id> --cd <project_root>
mi --home ~/.mind-incarnation claim apply-suggested <suggestion_id> --cd <project_root> --dry-run
```

Rollback claim-based preference tightening (append-only):

```bash
mi --home ~/.mind-incarnation claim retract <claim_id> --cd <project_root> --scope project
```

Manage Thought DB claims (project/global/effective):

- `mi claim list` supports filters: `--tag` (AND), `--contains`, `--type`, `--status`, `--as-of`, `--limit`.
- `mi claim show --graph` adds a bounded subgraph to the JSON output (inspection only).

```bash
mi --home ~/.mind-incarnation claim list --cd <project_root> --scope project
mi --home ~/.mind-incarnation claim list --cd <project_root> --scope global
mi --home ~/.mind-incarnation claim list --cd <project_root> --scope effective
mi --home ~/.mind-incarnation claim list --cd <project_root> --scope effective --type preference --tag values:base --contains "tests"

mi --home ~/.mind-incarnation claim show <claim_id> --cd <project_root> --scope effective
mi --home ~/.mind-incarnation claim show <claim_id> --cd <project_root> --scope effective --json --graph --depth 2 --direction both --edge-type depends_on
mi --home ~/.mind-incarnation claim mine --cd <project_root>

mi --home ~/.mind-incarnation claim retract <claim_id> --cd <project_root> --scope project
mi --home ~/.mind-incarnation claim supersede <claim_id> --cd <project_root> --text "..."
mi --home ~/.mind-incarnation claim same-as <dup_id> <canonical_id> --cd <project_root>
```

Manage Thought DB nodes (Decision/Action/Summary):

- `mi node list` supports filters: `--tag` (AND), `--contains`, `--type`, `--status`, `--limit`.
- `mi node show --graph` adds a bounded subgraph to the JSON output (inspection only).

```bash
mi --home ~/.mind-incarnation node list --cd <project_root> --scope project
mi --home ~/.mind-incarnation node list --cd <project_root> --scope global
mi --home ~/.mind-incarnation node list --cd <project_root> --scope effective
mi --home ~/.mind-incarnation node list --cd <project_root> --scope effective --type summary --tag values:summary

mi --home ~/.mind-incarnation node create --cd <project_root> --scope project --type decision --title "..." --text "..."
mi --home ~/.mind-incarnation node show <node_id> --cd <project_root> --scope effective --json
mi --home ~/.mind-incarnation node show <node_id> --cd <project_root> --scope effective --json --graph --depth 2 --direction both --edge-type derived_from
mi --home ~/.mind-incarnation node retract <node_id> --cd <project_root> --scope project
```

Manage Thought DB edges (project/global/effective):

```bash
mi --home ~/.mind-incarnation edge create --cd <project_root> --scope project --type depends_on --from <from_id> --to <to_id>

mi --home ~/.mind-incarnation edge list --cd <project_root> --scope project
mi --home ~/.mind-incarnation edge list --cd <project_root> --scope global
mi --home ~/.mind-incarnation edge list --cd <project_root> --scope effective

mi --home ~/.mind-incarnation edge list --cd <project_root> --scope project --type depends_on --from <event_id>
mi --home ~/.mind-incarnation edge show <edge_id> --cd <project_root>
```

Root-cause tracing (WhyTrace):

```bash
mi --home ~/.mind-incarnation why last --cd <project_root>
mi --home ~/.mind-incarnation why event <event_id> --cd <project_root>
mi --home ~/.mind-incarnation why claim <claim_id> --cd <project_root>
```

Manage workflows (project/global/effective):

```bash
mi --home ~/.mind-incarnation workflow list --cd <project_root> --scope project
mi --home ~/.mind-incarnation workflow list --cd <project_root> --scope global
mi --home ~/.mind-incarnation workflow list --cd <project_root> --scope effective

mi --home ~/.mind-incarnation workflow show <workflow_id> --cd <project_root> --scope effective --markdown
mi --home ~/.mind-incarnation workflow show <workflow_id> --cd <project_root> --scope effective --json

mi --home ~/.mind-incarnation workflow create --cd <project_root> --scope project --name "My workflow"
mi --home ~/.mind-incarnation workflow create --cd <project_root> --scope global --name "My global workflow"

mi --home ~/.mind-incarnation workflow edit <workflow_id> --cd <project_root> --scope effective --request "Change step 2 to run tests"

mi --home ~/.mind-incarnation workflow enable <workflow_id> --cd <project_root> --scope effective
mi --home ~/.mind-incarnation workflow disable <workflow_id> --cd <project_root> --scope effective

# Per-project override for a global workflow (does not edit the global file):
mi --home ~/.mind-incarnation workflow disable <workflow_id> --cd <project_root> --scope global --project-override
mi --home ~/.mind-incarnation workflow edit <workflow_id> --cd <project_root> --scope global --project-override --request "Patch step s2"
mi --home ~/.mind-incarnation workflow delete <workflow_id> --cd <project_root> --scope global --project-override  # clear override

mi --home ~/.mind-incarnation workflow delete <workflow_id> --cd <project_root> --scope project
mi --home ~/.mind-incarnation workflow delete <workflow_id> --cd <project_root> --scope global
```

Bind/sync host workspaces (derived artifacts, e.g., OpenClaw Skills):

```bash
mi --home ~/.mind-incarnation host list --cd <project_root>
mi --home ~/.mind-incarnation host bind openclaw --workspace <host_workspace_root> --cd <project_root>
mi --home ~/.mind-incarnation host sync --cd <project_root>
mi --home ~/.mind-incarnation host unbind openclaw --cd <project_root>
```

Notes:

- `mi workflow ...` auto-syncs to any bound host workspaces when `config.runtime.workflows.auto_sync_on_change=true` (default). Sync uses **effective enabled workflows** (project + global, with project precedence and optional per-project overrides for global workflows).
- The OpenClaw adapter exports enabled effective workflows as generated skill folders under `./.mi/generated/openclaw/skills/` and registers them under `./skills/` in the host workspace as symlinks (best-effort, reversible).

## Doc Update Policy (Source of Truth)

This spec is the source of truth for V1 behavior. Any functional changes MUST update:

- This file: `docs/mi-v1-spec.md`

Additionally, keep other user-facing docs aligned when impacted:

- `README.md` and `README.zh-CN.md` (CLI usage/examples, artifact locations, core principles)
- Any related `docs/*.md` files introduced later

Contributor note: this repo maintains a lightweight doc checklist at `references/doc-map.md` to reduce doc drift across iterative changes.

Doc sections to keep aligned:

- Runtime loop / constraints (if execution contract changes)
- Prompt pack (if new prompts are added or semantics change)
- Data models (if schemas/config change)
- CLI contract surface (commands/flags/output meaning). Internal handler refactors are allowed when behavior stays the same.

## Implementation Plan (V1)

1) Implement an MI wrapper CLI that runs Hands as a child process (default: `codex`; optional generic `cli` wrapper) and captures transcripts (no step slicing).
2) Persist artifacts per batch: transcripts and `EvidenceLog` (JSONL).
3) Implement prompt-pack calls via `codex exec --output-schema` (strict JSON parsing with safe fallbacks) and optional API-backed Mind providers with local schema validation.
4) Add post-hoc risk monitoring + optional interrupt/terminate mode (configurable).
5) Add project memory: store "no tests" verification strategy once per project and reuse.
