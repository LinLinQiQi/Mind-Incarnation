# MI Internals (V1)

This doc is for contributors. It intentionally does not describe user-facing behavior; use `docs/mi-v1-spec.md` for that.

## Where Things Live

High-level layering:

- CLI surface: `mi/cli_parser.py` + `mi/cli_parsers/`
- CLI dispatch/handlers: `mi/cli_dispatch.py` + `mi/cli_commands/`
- Runtime loop entrypoints: `mi/runtime/runner.py` + `mi/runtime/runner_core.py`
- Wiring composition: `mi/runtime/wiring/` + `mi/runtime/composition.py`
- Orchestration logic (flow helpers): `mi/runtime/autopilot/`
- Providers (Mind/Hands): `mi/providers/`
- Thought DB store + retrieval: `mi/thoughtdb/`
- Workflows + host adapters: `mi/workflows/`

## Runtime / Autopilot

The runtime loop is composed of small helpers under `mi/runtime/autopilot/` to keep behavior stable and reduce wiring drift.

Key areas:

- Hands execution flow: `mi/runtime/autopilot/hands_flow.py`
- Next input and loop-guard/break: `mi/runtime/autopilot/next_input_flow.py`, `mi/runtime/autopilot/loop_break_checks_flow.py`
- Evidence append/window/segment helpers: `mi/runtime/autopilot/evidence_flow.py`
- Decide-next prompt IO + records: `mi/runtime/autopilot/decide_query_flow.py`
- Auto-answer, risk policy, ask-user handling: `mi/runtime/autopilot/auto_answer_flow.py`, `mi/runtime/autopilot/risk_predecide.py`, `mi/runtime/autopilot/ask_user_flow.py`
- Cross-project recall write-through: `mi/runtime/autopilot/recall_flow.py`
- Checkpoints + mining + materialization: `mi/runtime/autopilot/checkpoint_pipeline.py`, `mi/runtime/autopilot/claim_mining_flow.py`, `mi/runtime/autopilot/node_materialize.py`
- Run-end flows: `mi/runtime/autopilot/learn_flow.py`, `mi/runtime/autopilot/why_trace_flow.py`

## Providers

- Hands runner(s): `mi/providers/hands_registry.py` + concrete runners (e.g., `mi/providers/codex_runner.py`)
- Mind providers: `mi/providers/mind_registry.py` + providers (OpenAI-compatible, Anthropic, Codex-schema)
- Transcript plumbing: `mi/providers/proc_stream.py`
- Interrupt support: `mi/providers/interrupts.py`

## Thought DB

Thought DB is append-only JSONL storage with:

- claims: `mi/thoughtdb/append_store.py` + view building in `mi/thoughtdb/view_store.py`
- app facade (used by CLI + runtime): `mi/thoughtdb/app_service.py`
- retrieval helpers: `mi/thoughtdb/retrieval.py`, `mi/thoughtdb/predicates.py`

## Docs Layout

- Behavior spec: `docs/mi-v1-spec.md` (source of truth)
- CLI guide: `docs/cli.md` / `docs/cli.zh-CN.md`
- Design notes: `docs/mi-thought-db.md`
- This doc: `docs/internals.md`

