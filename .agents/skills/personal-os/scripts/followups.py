#!/usr/bin/env python3
"""Manage Personal OS follow-up state; scheduling and delivery remain external.

All mutations use records.apply_bundle. `due` is read-only. A caller must obtain
confirmed delivery before invoking `mark-asked`; merely queuing a message is not
confirmation. Processing timestamps come from the real clock; actual event
timestamps may come from confirmed delivery receipts or source messages.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

import records


def utc_now():
    """Clock seam for deterministic tests, not a CLI mutation override."""
    return datetime.now(timezone.utc)


def aware(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be an ISO string with a timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def settings(root):
    path = Path(root) / "settings.json"
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    tz = ZoneInfo(cfg.get("timezone", "Asia/Shanghai"))
    evening_value = cfg.get("evening_time")
    evening = None
    if evening_value is not None:
        if not isinstance(evening_value, str) or not re.fullmatch(r"\d{2}:\d{2}", evening_value):
            raise ValueError("evening_time must be null or HH:MM")
        evening = time.fromisoformat(evening_value)
    weekly = cfg.get("weekly_review")
    if evening is not None and isinstance(weekly, dict) and weekly.get("merge_daily_review"):
        weekdays = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
        day, clock = weekly.get("day"), weekly.get("time")
        if day not in weekdays or not isinstance(clock, str) or not re.fullmatch(r"\d{2}:\d{2}", clock):
            raise ValueError("merged weekly_review needs day MO..SU and time HH:MM")
        evening = {"default": evening, "weekday": weekdays[day], "override": time.fromisoformat(clock)}
    cadence = cfg.get("followup_default_days", 7)
    if type(cadence) is not int or cadence != 7:
        raise ValueError("followup_default_days must be 7 under the agreed policy")
    return tz, evening, cadence


def stamp(now, tz):
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock must return an aware datetime")
    return now.astimezone(tz).isoformat(timespec="seconds")


def evening_for(day, evening):
    if isinstance(evening, dict):
        return evening["override"] if day.weekday() == evening["weekday"] else evening["default"]
    return evening


def at_evening(day, evening, tz):
    return datetime.combine(day, evening_for(day, evening), tzinfo=tz)


def expected_time(value):
    if value is None:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    return aware(value)


def next_check(now, tz, evening, cadence, explicit=None, expected=None):
    if explicit is not None:
        return stamp(aware(explicit), tz)
    expectation = expected_time(expected)
    if evening is None:
        return None
    local = now.astimezone(tz)
    if expectation is None:
        target = at_evening(local.date() + timedelta(days=cadence), evening, tz)
    elif isinstance(expectation, datetime):
        expectation = expectation.astimezone(tz)
        target = at_evening(expectation.date(), evening, tz)
        if target < expectation:
            target = at_evening(target.date() + timedelta(days=1), evening, tz)
    else:
        target = at_evening(expectation, evening, tz)
    # A result date in the past remains eligible at the next nightly batch.
    if target < now:
        target = at_evening(local.date(), evening, tz)
        if target < now:
            target = at_evening(target.date() + timedelta(days=1), evening, tz)
    return stamp(target, tz)


def nonempty(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def source_event(value, now):
    event = aware(value) if value is not None else None
    if event is not None and event > now:
        raise ValueError("source event time cannot be in the future")
    return event


def regression(meta, event_time, actions):
    """Do not let a delayed import rewrite a newer known conversation state."""
    if event_time is None:
        return None
    for action, field in (("asked", "last_asked_at"), ("reply", "last_answered_at")):
        if action in actions and meta.get(field) is not None and event_time < aware(meta[field]):
            return "source_event_precedes_newer_state"
    for prior in reversed(meta.get("history", [])):
        if prior.get("action") not in actions:
            continue
        source_time = prior.get("answered_at") if prior["action"] == "reply" else prior.get("delivered_at")
        if source_time is not None:
            if event_time < aware(source_time):
                return "source_event_precedes_newer_state"
        elif prior.get("recorded_at") is not None and event_time <= aware(prior["recorded_at"]):
            return "event_order_unknown_against_previous_unsourced_event"
    return None


def needs_review(reason, candidate):
    return {"status": "needs_review", "reason": reason, "candidate": candidate, "persisted": False}


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,95}", value):
        raise ValueError("id must be a lowercase slug, letters/numbers/hyphens, up to 96 characters")
    return value


def find_document(root, document_id, document_type):
    safe_id(document_id)
    dirname = {"followup": "followups", "case": "cases", "record": "records"}[document_type]
    found = []
    for path in sorted((Path(root) / "data" / dirname).rglob("*.md")):
        original = path.read_bytes()
        meta, body = records.read_document(path)
        if original != path.read_bytes():
            raise ValueError(f"document changed during read; retry: {path}")
        if meta.get("id") == document_id:
            if meta.get("document_type") != document_type:
                raise ValueError(f"wrong document_type for {document_id}")
            errors = records.validate_document(meta, body, path.relative_to(Path(root)).as_posix())
            if errors:
                raise ValueError("; ".join(errors))
            found.append((path, meta, body, hashlib.sha256(original).hexdigest()))
    if len(found) != 1:
        raise ValueError(f"expected one {document_type} with id {document_id}; found {len(found)}")
    return found[0]


def change(root, path, meta, body, expected_sha256):
    return {
        "path": str(path.relative_to(Path(root))),
        "metadata": meta,
        "body": body,
        "expected_sha256": expected_sha256,
    }


def mutate(root, operation_id, action, arguments, build):
    context = {"component": "followups", "action": action, "arguments": arguments}
    prior = records.get_operation_bundle(root, operation_id)
    if prior is not None:
        if prior.get("context") != context:
            raise ValueError("operation_id already belongs to a different request")
        return records.apply_bundle(root, prior, operation_id)
    proposed = build()
    if isinstance(proposed, dict):
        return proposed  # A previously delivered check is already represented.
    return records.apply_bundle(root, {"context": context, "changes": proposed}, operation_id)


def create(root, followup_id, case_id, question, operation_id, expected_result_at=None, next_check_at=None):
    safe_id(followup_id)
    nonempty(question, "question")
    expected_time(expected_result_at)
    if next_check_at is not None:
        aware(next_check_at)
    args = dict(id=followup_id, case_id=case_id, question=question,
                expected_result_at=expected_result_at, next_check_at=next_check_at)

    def build():
        find_document(root, case_id, "case")
        tz, evening, cadence = settings(root)
        if evening is None and next_check_at is None:
            return {"status": "blocked", "reason": "evening_time_not_configured",
                    "candidate": args, "persisted": False, "scheduler_created": False}
        now = utc_now()
        timestamp = stamp(now, tz)
        path = Path(root) / "data" / "followups" / f"{followup_id}.md"
        # Also catch an existing ID stored in a nested or renamed file.
        for existing in (Path(root) / "data" / "followups").rglob("*.md"):
            if records.read_document(existing)[0].get("id") == followup_id:
                raise ValueError(f"followup already exists: {followup_id}")
        meta = dict(schema_version=1, id=followup_id, document_type="followup",
                    recorded_at=timestamp, updated_at=timestamp, case_id=case_id,
                    question=question, expected_result_at=expected_result_at,
                    next_check_at=next_check(now, tz, evening, cadence, next_check_at, expected_result_at),
                    status="active", contact_state="not_asked", answer_state="unknown",
                    last_asked_at=None, last_answered_at=None, cadence_days=cadence,
                    outcome_refs=[], history=[{"recorded_at": timestamp, "action": "created"}])
        original_hash = None  # Creation must never overwrite a colliding path.
        body = "## 待回填问题\n" + question + "\n\n## 运行边界\n到期清单不代表已发送；只有确认送达后才记录询问。实际结果单独建档并关联。\n"
        return [change(root, path, meta, body, original_hash)]

    return mutate(root, operation_id, "create", args, build)


def due(root, at=None):
    tz, evening, _ = settings(root)
    now = aware(at) if at is not None else utc_now()
    local = now.astimezone(tz)
    base = dict(checked_at=stamp(now, tz), scheduler_created=False, due=[])
    if evening is None:
        return dict(base, status="blocked", reason="evening_time_not_configured")
    if local.time().replace(tzinfo=None) < evening_for(local.date(), evening):
        return dict(base, status="outside_evening_window")
    problems = records.validate_root(root)
    if problems:
        return dict(base, status="needs_review", invalid=problems)
    invalid = []
    for path in sorted((Path(root) / "data" / "followups").rglob("*.md")):
        try:
            meta, _ = records.read_document(path)
            if meta.get("document_type") != "followup":
                raise ValueError("wrong document_type")
            if meta.get("status") not in ("active", "paused", "closed"):
                raise ValueError("invalid status")
            if meta.get("contact_state") not in ("not_asked", "awaiting_reply", "replied"):
                raise ValueError("invalid contact_state")
            if meta.get("answer_state") not in ("unknown", "no_result", "partial", "complete", "unavailable"):
                raise ValueError("invalid answer_state")
            safe_id(meta.get("id"))
            nonempty(meta.get("question"), "question")
            find_document(root, meta.get("case_id"), "case")
            scheduled = meta.get("next_check_at")
            if meta["status"] == "closed" or scheduled is None:
                continue
            if aware(scheduled) <= now:
                item = {key: meta[key] for key in
                        ("id", "case_id", "question", "status", "contact_state", "answer_state", "next_check_at")}
                item["known_outcome_refs"] = list(meta.get("outcome_refs", []))
                item["last_answered_at"] = meta.get("last_answered_at")
                item["recommendation_refs"] = list(meta.get("recommendation_refs", []))
                base["due"].append(item)
        except (ValueError, KeyError, TypeError) as exc:
            invalid.append({"path": str(path.relative_to(Path(root))), "error": str(exc)})
    return dict(base, status="ready" if not invalid else "needs_review", invalid=invalid)


def mark_asked(root, followup_id, check_id, delivery_ref, operation_id, delivered_at=None):
    nonempty(check_id, "check_id")
    nonempty(delivery_ref, "delivery_ref (confirmed external delivery)")
    if delivered_at is not None:
        aware(delivered_at)
    args = dict(id=followup_id, check_id=check_id, delivery_ref=delivery_ref, delivered_at=delivered_at)

    def build():
        path, meta, body, original_hash = find_document(root, followup_id, "followup")
        history = meta.setdefault("history", [])
        for event in history:
            if event.get("action") == "asked" and event.get("check_id") == check_id:
                if event.get("delivery_ref") != delivery_ref or (delivered_at is not None and
                        (event.get("delivered_at") is None or aware(event["delivered_at"]) != aware(delivered_at))):
                    raise ValueError("check_id has different confirmed delivery evidence")
                return {"status": "already_recorded", "id": followup_id, "check_id": check_id,
                        "notification_sent_by_script": False}
        if meta["status"] == "closed":
            raise ValueError("cannot mark a closed followup as asked")
        tz, evening, _ = settings(root)
        if evening is None:
            return {"status": "blocked", "reason": "evening_time_not_configured",
                    "candidate": args, "persisted": False, "scheduler_created": False}
        now = utc_now()
        event_time = source_event(delivered_at, now)
        issue = regression(meta, event_time, {"asked", "reply"})
        if issue:
            return needs_review(issue, args)
        if meta["status"] == "paused" and (meta.get("next_check_at") is None or aware(meta["next_check_at"]) > now):
            raise ValueError("followup is paused and not due for resumption")
        timestamp = stamp(now, tz)
        event_stamp = stamp(event_time, tz) if event_time is not None else None
        meta.update(status="active", contact_state="awaiting_reply", last_asked_at=event_stamp,
                    updated_at=timestamp,
                    next_check_at=next_check(event_time or now, tz, evening, meta["cadence_days"]))
        # Existing answer_state describes the last received answer, not silence.
        history.append(dict(recorded_at=timestamp, action="asked", check_id=check_id,
                            delivery_ref=delivery_ref, delivered_at=event_stamp,
                            time_basis="source" if event_time is not None else "processing_time_unknown_event",
                            source_limitation=None if event_time is not None else
                            "Delivery confirmed, but actual delivery time is unknown; scheduling uses processing time."))
        return [change(root, path, meta, body, original_hash)]

    return mutate(root, operation_id, "mark-asked", args, build)


def reply(root, followup_id, answer, operation_id, next_check_at=None, outcome_ids=None,
          expected_result_at=None, answered_at=None, source_ref=None):
    if answer not in ("no_result", "partial", "complete", "unavailable", "pause", "stop"):
        raise ValueError("unsupported answer")
    outcomes = sorted(set(outcome_ids or []))
    if answer in ("partial", "complete") and not outcomes:
        raise ValueError("partial and complete require independently persisted outcome record IDs")
    if answer in ("no_result", "pause", "stop") and outcomes:
        raise ValueError("this reply state cannot add outcome records")
    if answer in ("complete", "unavailable", "stop") and (next_check_at is not None or expected_result_at is not None):
        raise ValueError("closed followups cannot have a next check or new expected date")
    if answer == "pause" and expected_result_at is not None:
        raise ValueError("use next_check_at to specify a pause end")
    if next_check_at is not None:
        aware(next_check_at)
    if source_ref is not None:
        nonempty(source_ref, "source_ref")
    if answered_at is not None:
        aware(answered_at)
        nonempty(source_ref, "source_ref is required with answered_at")
    expected_time(expected_result_at)
    args = dict(id=followup_id, answer=answer, next_check_at=next_check_at,
                outcome_ids=outcomes, expected_result_at=expected_result_at,
                answered_at=answered_at, source_ref=source_ref)

    def build():
        path, meta, body, original_hash = find_document(root, followup_id, "followup")
        now = utc_now()
        event_time = source_event(answered_at, now)
        issue = regression(meta, event_time, {"asked", "reply"})
        if issue:
            return needs_review(issue, args)
        if meta["status"] == "closed":
            raise ValueError("followup is closed; create a new followup for a new observation")
        for outcome in outcomes:
            _, outcome_meta, _, _ = find_document(root, outcome, "record")
            if outcome_meta.get("kind") != "outcome":
                raise ValueError(f"{outcome} must be an outcome record")
        tz, evening, _ = settings(root)
        if answer in ("no_result", "partial") and evening is None and next_check_at is None:
            return {"status": "blocked", "reason": "evening_time_not_configured",
                    "candidate": args, "persisted": False, "scheduler_created": False}
        timestamp = stamp(now, tz)
        event_stamp = stamp(event_time, tz) if event_time is not None else None
        schedule_anchor = event_time or now
        meta.update(updated_at=timestamp, last_answered_at=event_stamp, contact_state="replied")
        if expected_result_at is not None:
            meta["expected_result_at"] = expected_result_at
        if answer in ("complete", "unavailable", "stop"):
            meta.update(status="closed", next_check_at=None)
            if answer != "stop":
                meta["answer_state"] = answer
        elif answer == "pause":
            meta.update(status="paused", next_check_at=stamp(aware(next_check_at), tz) if next_check_at else None)
        else:
            scheduling_expectation = expected_result_at
            prior_expectation = expected_time(meta.get("expected_result_at"))
            if answer != "no_result" and scheduling_expectation is None and prior_expectation is not None:
                # Partial progress may retain an upcoming expectation. An explicit
                # no-result reply without a NEW time always starts a weekly
                # interval; retain the old expectation only as historical context.
                scheduled_evening = None
                if evening is not None:
                    expected_day = (prior_expectation.astimezone(tz).date()
                                    if isinstance(prior_expectation, datetime) else prior_expectation)
                    scheduled_evening = at_evening(expected_day, evening, tz)
                    if isinstance(prior_expectation, datetime) and scheduled_evening < prior_expectation:
                        scheduled_evening = at_evening(scheduled_evening.date() + timedelta(days=1), evening, tz)
                if scheduled_evening is not None and scheduled_evening > schedule_anchor:
                    scheduling_expectation = meta["expected_result_at"]
            meta.update(status="active", answer_state=answer,
                        next_check_at=next_check(schedule_anchor, tz, evening, meta["cadence_days"], next_check_at, scheduling_expectation))
        meta["outcome_refs"] = list(dict.fromkeys(meta.get("outcome_refs", []) + outcomes))
        meta.setdefault("history", []).append(dict(recorded_at=timestamp, action="reply", answer=answer,
                                                  answered_at=event_stamp, source_ref=source_ref,
                                                  time_basis="source" if event_time is not None else "processing_time_unknown_event",
                                                  source_limitation=(None if event_time is not None else
                                                      "Original reply time is unknown; scheduling uses processing time." if source_ref else
                                                      "Original reply source and event time are unknown; scheduling uses processing time."),
                                                  outcome_refs=outcomes, next_check_at=meta["next_check_at"]))
        return [change(root, path, meta, body, original_hash)]

    return mutate(root, operation_id, "reply", args, build)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "due", "mark-asked", "reply"):
        command = commands.add_parser(name)
        command.add_argument("--root", required=True, type=Path)
        if name != "due":
            command.add_argument("--id", required=True)
            command.add_argument("--operation-id", required=True)
        if name in ("create", "reply"):
            command.add_argument("--expected-result-at")
            command.add_argument("--next-check-at")
        if name == "create":
            command.add_argument("--case-id", required=True)
            command.add_argument("--question", required=True)
        elif name == "due":
            command.add_argument("--at", help="Read-only preview time; must include timezone")
        elif name == "mark-asked":
            command.add_argument("--check-id", required=True)
            command.add_argument("--delivery-ref", required=True)
            command.add_argument("--delivered-at", help="Actual aware delivery time from the confirmed receipt")
        else:
            command.add_argument("--answer", required=True, choices=("no_result", "partial", "complete", "unavailable", "pause", "stop"))
            command.add_argument("--outcome-id", action="append", default=[])
            command.add_argument("--answered-at", help="Actual aware reply time from the source message")
            command.add_argument("--source-ref", help="Source message reference; required with answered-at")
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    if "id" in args:
        args["followup_id"] = args.pop("id")
    if "outcome_id" in args:
        args["outcome_ids"] = args.pop("outcome_id")
    try:
        result = {"create": create, "due": due, "mark-asked": mark_asked, "reply": reply}[command](**args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
