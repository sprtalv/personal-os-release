---
name: personal-os
description: Organize selected personal evidence, plans, decisions, corrections, and follow-up outcomes in a private data repository.
---

# Personal OS

This public repository contains a workflow and local scripts. It contains no personal archive and does not install an assistant hook, schedule, notification channel, cloud binding, or backup. Keep all real personal data in a separate private data root.

## Workflow

1. Check whether an interaction contains meaningful new information. Ordinary conversation need not become a record.
2. Separate user statements, external observations, user hypotheses, and AI hypotheses. Preserve source, context, uncertainty, and event versus expression time. Do not infer a stable trait or causal relationship from one event.
3. Search current and historical records before adding or changing evidence. Read source and linked records; keyword hits alone are insufficient. See [retrieval](references/retrieval.md).
4. For a new fact, plan, or correction, follow [capture](references/capture.md) and the [data contract](references/memory.md). For an observable outcome, create a specific question using [follow-up](references/followup.md).
5. After an outcome, first save an outcome record, then link the case and follow-up. Reconsider the expectation, contrary evidence, and current summaries. See [review](references/review.md).
6. For daily planning, use [daily](references/daily.md). Confirm live tasks and calendar events through separately authorized tools before changing external arrangements.
7. Validate candidate records and report actual results. Use scripts only with an explicitly chosen private data root and appropriate authorization. See [operations](references/operations.md).

## Files and limits

The private data root may contain data/records/, data/current/, data/cases/, data/followups/, and reports/. This public Skill directory supplies templates and scripts only. Never put real user records, credentials, run receipts, or local settings in this public repository.

From this Skill directory, run python scripts/records.py --help and python scripts/followups.py --help for commands. records.py search is keyword-based; apply makes local changes. followups.py due only lists due questions. Neither script sends messages or schedules runs. publish.py is optional for a separately configured private Git data root; inspect its plan before any --execute use. A local save is not remote verification.

An assistant may follow this Skill when its host actually makes the file accessible. Cloning it does not prove automatic invocation, continuous chat capture, proactive delivery, backup, or improved long-term advice.
