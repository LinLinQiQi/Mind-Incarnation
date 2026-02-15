# MI Thought DB (Design Notes)

Status: design (not implemented in V1)
Last updated: 2026-02-15

This document captures the "Thought DB" direction for Mind Incarnation (MI):

- A time-indexed, provenance-traceable database of thinking
- Atomic reusable `Claim`s as the "basic arguments"
- A derived, evolvable graph structure for root-cause / "why" tracing
- Minimal privacy labeling (`visibility`) that still allows Mind model usage when configured

It is intentionally written as a stable reference to prevent multi-iteration context loss.

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

The Thought DB direction adds:

- A project/global Claim store (atomic claims)
- A ClaimGraph store (edges + redirect mappings)
- Retrieval that returns a small subgraph + provenance, not just text snippets

V1 can remain text-only; the Thought DB is an extension that should be implemented as:

- A new memory backend (or backend+service) with the same `MemoryBackend` interface, or
- A parallel "graph memory" service used by recall + root-cause queries

