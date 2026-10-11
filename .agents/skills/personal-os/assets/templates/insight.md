---
schema_version: 1
id: "{{id}}"
document_type: record
kind: insight
author: "{{user|ai}}"
basis: "{{user_statement|ai_inference}}"
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
context: "{{applies when; include conditions}}"
valid_from: null
valid_until: null
user_endorsement: pending
evidence_state: untested
evidence_refs: []
counterexample_refs: []
revises: []
unknowns: []
---

## Current understanding
{{State the claim as a bounded understanding, not a personality verdict.}}

## Evidence and alternatives
{{Link original evidence; note counterexamples and plausible alternatives.}}

## Revision boundary
{{What changed, what remains unknown, and when to revisit. If source.ref is unavailable, preserve a short source excerpt and explicitly state that limitation.}}
