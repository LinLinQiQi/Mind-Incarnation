# MI Thought DB (Design Notes)

Status: implemented (partial, V1)
Last updated: 2026-02-17

This document captures the "Thought DB" direction for Mind Incarnation (MI):

- A time-indexed, provenance-traceable database of thinking
- Atomic reusable `Claim`s as the "basic arguments"
- A derived, evolvable graph structure for root-cause / "why" tracing
- Minimal privacy labeling (`visibility`) that still allows Mind model usage when configured

It is intentionally written as a stable reference to prevent multi-iteration context loss.

## V1 Implementation (What Exists Today)

Implemented in V1 (incremental; safe foundation):

- Append-only Claim + Edge stores (project + global) with `source_refs` that cite **EvidenceLog `event_id` only**
- Append-only Node store (project + global) for `Decision` / `Action` / `Summary` nodes (first-class IDs; append-only; cites EvidenceLog `event_id` only)
- Global values/preferences are represented canonically in Thought DB as:
  - a global EvidenceLog event `kind=values_set` in `global/evidence.jsonl` (provenance anchor),
  - a raw values preference Claim tagged `values:raw` (audit; excluded from runtime injection/recall),
  - a global Summary node tagged `values:summary` (human-facing; compiled from the values prompt),
  - derived global preference/goal Claims tagged `values:base` + `values_set:<event_id>` (runtime; produced via `values_claim_patch` during `mi init` / `mi values set`).
- Operational defaults (MI behavior switches like `ask_when_uncertain` / `refactor_intent`) are canonically stored as global preference Claims tagged `mi:setting:*` and pinned into compact contexts; `mi run` seeds missing claims (append-only) by writing/using a global `mi_defaults_set` event in `global/evidence.jsonl` for provenance. Use `mi settings set` to update them explicitly.
- Preference tightening suggestions (`learned_changes`, legacy field name) are canonically materialized as Thought DB preference Claims when `violation_response.auto_learn=true` (append-only, reversible via claim retraction). Legacy `learned.jsonl` files are ignored by current MI versions (keep only for manual migration/audit).
- Checkpoint-only, high-threshold claim mining during `mi run` (no per-step protocol; no user prompts)
- Deterministic checkpoint materialization of `Decision` / `Action` / `Summary` nodes during `mi run` (no extra model calls; best-effort; append-only)
- When the model outputs high-confidence edges, MI also appends `Edge` records (best-effort; scoped to project/global).
- On-demand mining + basic management via CLI (`mi claim ...`)
- On-demand root-cause tracing via `mi why ...`: selects a minimal support set of claim ids for an EvidenceLog `event_id` (and may materialize `depends_on(event_id -> claim_id)` edges).
- Manual node/edge management via CLI (`mi node ...`, `mi edge ...`)
- Memory index ingestion of **active canonical** claims (`kind=claim`) and nodes (`kind=node`) for optional text recall/search

Not implemented yet (future direction):

- LLM-based mining/extraction + de-duplication of Decision/Action/Summary nodes from EvidenceLog / transcripts (beyond the deterministic checkpoint materialization)
- Whole-graph LLM refactors via validated patch application (subgraph retrieval -> patch -> validate -> apply)

## Problem / Goal

We want a system that can:

- Record thinking over time (inputs, outputs, "thought actions", transformations)
- Extract atomic reusable arguments (`Claim`s) and connect them
- Support dynamic updates / expiration (things can become invalid)
- Provide multi-scope abstraction summaries (project vs global; coarse vs fine)
- Enable on-demand retrieval ("bring back the right reasons") without dumping the whole history
- Explain decisions/actions with traceable dependencies ("root cause tracing")
- Allow LLM-assisted restructuring without corrupting the source of truth

## Key Principles

1) Source of truth is append-only
- Never overwrite history. Updates are new events/nodes with explicit relationships.

2) Derived structure is versioned and auditable
- Graph edges/nodes created by an LLM are "proposals" applied as patches.
- Every derived node/edge MUST carry `source_refs` to trace back to raw events.

3) Stable identifiers, no broken references
- Cross references point to stable IDs (not to text).
- Updates use `supersedes` / `same_as` / `retracted` rather than deleting or mutating.

4) Temporal semantics are first-class
- Keep both:
  - `asserted_ts`: when the claim was asserted/recorded
  - `valid_from` / `valid_to`: when the claim is intended to be valid in the real world

5) Minimal privacy labeling
- `visibility` is a label: `private | project | global`
- Design choice (user-confirmed): `private` MAY be used by the Mind model (not encrypted), but it is still labeled for future policy control and audits.

## Data Model (Minimal)

### Event Ledger (immutable)

Append-only time series; everything starts here.

Minimal event shape:

```json
{
  "event_id": "string",
  "ts": "RFC3339",
  "actor": "user|hands|mind|mi",
  "visibility": "private|project|global",
  "kind": "user_input|hands_output|evidence|risk_event|...|thought_action",
  "payload": {}
}
```

### Claim (atomic argument)

Atomic claims are the reusable "basic arguments" used to justify decisions/actions.

```json
{
  "claim_id": "string",
  "claim_type": "fact|preference|assumption|goal",
  "text": "string",
  "visibility": "private|project|global",
  "scope": "project|global",
  "project_id": "string (required when scope=project)",
  "asserted_ts": "RFC3339",
  "valid_from": "RFC3339|null",
  "valid_to": "RFC3339|null",
  "status": "active|superseded|retracted",
  "source_refs": [{"kind":"event", "event_id":"..."}],
  "tags": ["string"]
}
```

Notes:

- `asserted_ts` answers "when did we believe/record this?"
- `valid_*` answers "for which time window is this intended to be true/applicable?"
- If a claim is updated, the old one is NOT edited; it is superseded.

### Edges (dependencies + evolution)

Edges connect claims and higher-level nodes (Decision/Action/Summary).

```json
{
  "edge_id": "string",
  "edge_type": "depends_on|supports|contradicts|derived_from|mentions|supersedes|same_as",
  "from_id": "node_id",
  "to_id": "node_id",
  "visibility": "private|project|global",
  "asserted_ts": "RFC3339",
  "source_refs": [{"kind":"event", "event_id":"..."}],
  "notes": "string"
}
```

Node kinds beyond Claim (minimal):

- `Decision`: a chosen path; should depend on claims + values
- `Action`: an executed or intended action; should depend on claims + decisions
- `Summary`: an abstraction node; should `derived_from` a set of nodes/edges

## Root-Cause Tracing ("Why?")

Given a target `Decision` or `Action`, we want:

- The dependency closure: all relevant supporting claims (bounded by budget)
- The minimal support set: the smallest set of claims that explain the action/decision

Suggested algorithm (best-effort):

1) Choose an "as-of time" `t` (default: now)
2) Collect a candidate subgraph by reverse-walking `depends_on/supports/derived_from`
   - Apply budgets: max depth, max nodes, time window
   - Filter/penalize nodes whose `valid_*` window does not include `t`
   - Apply `visibility` policy for what may be used
3) Ask the Mind model to pick the minimal support set within the candidate subgraph:
   - Output: referenced `claim_id`s + short explanation + confidence
4) Store the explanation as a derived node (`Summary` or `WhyTrace`) with `source_refs`

## Dynamic Updates / Expiration

Dynamic changes must not break references.

- Update a claim by creating a new claim and linking:
  - `supersedes(old_claim -> new_claim)`
- Optionally set `valid_to` on the old claim when known.
- For de-duplication / consolidation:
  - `same_as(duplicate -> canonical)`
  - Query/recall canonicalizes via redirect mapping.
- For retractions:
  - mark `status=retracted` and keep the node for audit.

Cycles are allowed (real reasoning is not always acyclic).
For retrieval/explanations, collapse SCCs (strongly connected components) into a cluster node or a `Summary`.

## LLM-Assisted Graph Restructuring (Safe "Whole-Graph Refactors")

Do NOT feed the entire graph to the model.

Instead:

1) Retrieve a relevant subgraph (seed with search hits; expand by neighbors)
2) Ask the model to output a structured patch:
   - create_node / create_edge
   - supersede / same_as / retract
   - create_summary (multi-level abstractions)
3) Locally validate the patch:
   - referenced IDs must exist
   - every change must include `source_refs`
   - no in-place mutation of raw EventLedger
4) Apply as a new derived version (rollback-able)

## Integration with MI (Roadmap Direction)

MI already has:

- Event-like evidence ledger (EvidenceLog with stable `event_id`)
- A materialized text index for recall

The Thought DB adds:

- A project/global Claim store (atomic claims)
- A ClaimGraph store (edges + redirect mappings)
- Retrieval that returns a small subgraph + provenance, not just text snippets

### Storage Layout (V1)

Project-scoped (per `project_id`):

- `~/.mind-incarnation/projects/<project_id>/thoughtdb/claims.jsonl`
- `~/.mind-incarnation/projects/<project_id>/thoughtdb/edges.jsonl`
- `~/.mind-incarnation/projects/<project_id>/thoughtdb/nodes.jsonl`

Global (shared across projects):

- `~/.mind-incarnation/thoughtdb/global/claims.jsonl`
- `~/.mind-incarnation/thoughtdb/global/edges.jsonl`
- `~/.mind-incarnation/thoughtdb/global/nodes.jsonl`

### CLI (V1)

- `mi claim list --cd <project>` (default: active + canonical)
- `mi claim show <claim_id> --cd <project>`
- `mi claim mine --cd <project>` (on-demand, best-effort; uses current segment buffer or EvidenceLog tail)
- `mi claim retract <claim_id> --cd <project>`
- `mi claim supersede <old_claim_id> --text "..." --cd <project>`
- `mi claim same-as <dup_id> <canonical_id> --cd <project>`
- `mi node create --type decision|action|summary --text "..." --cd <project>` (append-only)
- `mi node list --cd <project>` / `mi node show <node_id> --cd <project>` / `mi node retract <node_id> --cd <project>`
- `mi edge create --type depends_on|supports|... --from <id> --to <id> --cd <project>` (append-only)
- `mi edge list --cd <project>` (filterable by `--type/--from/--to`; default scope=project)
- `mi edge show <edge_id> --cd <project>`
- `mi why last --cd <project>` / `mi why event <event_id> --cd <project>` / `mi why claim <claim_id> --cd <project>`

### Mining Trigger (V1)

- Default: checkpoint-only (`checkpoint_decide` determines a boundary; MI then mines workflows/preferences and also mines claims).
- No user prompts. Mining is internal and append-only.

### Memory Index (V1)

- Claims are indexed as `kind=claim` (active, canonical only).
- Cross-project recall can include claims only when `config.runtime.cross_project_recall.include_kinds` contains `"claim"` (default is conservative and may omit it).

V1 can still be "text-only recall" for most flows; the Thought DB is an extension implemented as:

- A new memory backend (or backend+service) with the same `MemoryBackend` interface, or
- A parallel "graph memory" service used by recall + root-cause queries
