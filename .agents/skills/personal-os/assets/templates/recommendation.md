---
schema_version: 1
id: "{{id}}"
document_type: record
kind: recommendation
author: ai
basis: ai_inference
domains: []
status: active
source:
  ref: null
  message_at: null
  excerpt: null
time:
  event: null
  expressed: null
  target: null
  precision: unknown
  note: null
related: []
context: "{{situation and applicable constraints}}"
expected_result: "{{original observable expectation}}"
adoption_state: unknown
adoption_ref: null
evidence_refs: []
counterexample_refs: []
process_refs: []
outcome_refs: []
side_effect_refs: []
revision_refs: []
revises: []
unknowns: []
---

## Suggestion and basis
{{Describe the advice, its evidence, alternatives and possible cost.}}

## User choice and actual process
{{Unknown does not mean rejected. If adopted or declined, link the user's own decision or outcome.}}

## Result and revision
{{Link actual outcomes and side effects. Preserve the original expected_result; revise advice with a new record. If source.ref is unavailable, state that limitation.}}
