#!/usr/bin/env python3
"""Validate and locally save Personal OS Markdown records; no Git/network writes.

All cross-file changes are checked before any personal file is written. A durable
journal makes interrupted multi-file changes recoverable, not magically atomic.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
import unicodedata
from zoneinfo import ZoneInfo

import yaml

ID = re.compile(r"[a-z0-9][a-z0-9-]{0,95}\Z")
DOMAINS = {
    "background", "study_research", "goals_preferences", "views_values",
    "experiences_decisions", "wellbeing_health", "relationships", "finance",
}
KINDS = {
    "experience", "observation", "feeling", "view", "intention", "decision",
    "hypothesis", "outcome", "correction", "insight", "recommendation",
}
PATHS = {
    "record": r"data/records/(\d{4}-\d{2})/([a-z0-9][a-z0-9-]{0,95})\.md",
    "case": r"data/cases/([a-z0-9][a-z0-9-]{0,95})\.md",
    "followup": r"data/followups/([a-z0-9][a-z0-9-]{0,95})\.md",
    "summary": r"data/current/([a-z0-9][a-z0-9-]{0,95})\.md",
    "report": r"reports/([a-z0-9][a-z0-9-]{0,95})\.md",
}
DIRECTORIES = ("data/records", "data/cases", "data/followups", "data/current", "reports")


class StrictLoader(yaml.SafeLoader):
    """Reject duplicate YAML keys instead of silently dropping earlier values."""


def _mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ValueError("YAML keys must be scalar") from exc
        if duplicate:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def _now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="microseconds")


def _aware(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.tzinfo is not None and parsed.utcoffset() is not None
    except ValueError:
        return False


def _temporal(value):
    if not isinstance(value, str):
        return False
    if _aware(value):
        return True
    try:
        if re.fullmatch(r"\d{4}-\d{2}", value):
            date.fromisoformat(value + "-01")
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            date.fromisoformat(value)
        else:
            return False
        return True
    except ValueError:
        return False


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)


def read_document(path):
    text = Path(path).read_text(encoding="utf-8")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)(.*)\Z", text, re.S)
    if not match:
        raise ValueError(f"Missing YAML frontmatter: {path}")
    metadata = yaml.load(match.group(1), Loader=StrictLoader)
    if not isinstance(metadata, dict):
        raise ValueError(f"Frontmatter must be an object: {path}")
    return metadata, match.group(2).strip()


def render_document(meta, body):
    if not isinstance(meta, dict) or not isinstance(body, str):
        raise ValueError("Metadata must be an object and body a string")
    return "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n\n" + body.strip() + "\n"


def _path_type(path):
    value = str(path)
    for document_type, pattern in PATHS.items():
        match = re.fullmatch(pattern, value)
        if match:
            if document_type == "record":
                date.fromisoformat(match.group(1) + "-01")
            return document_type
    raise ValueError(f"Not an allowed personal-document path: {value}")


def validate_document(meta, body, path=None):
    errors = []
    if not isinstance(meta, dict):
        return ["Metadata must be an object"]
    if not _nonempty(body):
        errors.append("Markdown body must be nonempty")
    if type(meta.get("schema_version")) is not int or meta["schema_version"] != 1:
        errors.append("schema_version must be 1")
    if not isinstance(meta.get("id"), str) or not ID.fullmatch(meta["id"]):
        errors.append("id must be a safe lowercase slug (1–96 characters)")
    document_type = meta.get("document_type")
    if not isinstance(document_type, str):
        document_type = ""
    if document_type not in PATHS:
        errors.append("Invalid document_type")
    if path is not None:
        try:
            if _path_type(path) != document_type:
                errors.append("document_type does not match directory")
            if PurePosixPath(str(path)).stem != meta.get("id"):
                errors.append("Filename must match id")
        except ValueError as exc:
            errors.append(str(exc))
    for field in ("recorded_at", "updated_at"):
        if not _aware(meta.get(field)):
            errors.append(f"{field} must be a quoted timezone-aware ISO8601 string")
    if all(_aware(meta.get(field)) for field in ("recorded_at", "updated_at")):
        if datetime.fromisoformat(meta["updated_at"].replace("Z", "+00:00")) < datetime.fromisoformat(meta["recorded_at"].replace("Z", "+00:00")):
            errors.append("updated_at cannot precede recorded_at")

    def choice(field, allowed):
        if not isinstance(meta.get(field), str) or meta[field] not in allowed:
            errors.append(f"Invalid {field}: expected one of {', '.join(sorted(allowed))}")

    def refs(field):
        values = meta.get(field)
        if not isinstance(values, list) or any(not isinstance(v, str) or not ID.fullmatch(v) for v in values):
            errors.append(f"{field} must be a list of safe record IDs")
        elif len(values) != len(set(values)):
            errors.append(f"{field} contains duplicate IDs")

    def strings(field):
        values = meta.get(field)
        if not isinstance(values, list) or any(not _nonempty(v) for v in values):
            errors.append(f"{field} must be a list of nonempty strings")

    if document_type == "record":
        choice("kind", KINDS)
        choice("author", {"user", "ai", "external"})
        choice("basis", {"user_statement", "tool_observation", "ai_inference", "imported_source"})
        choice("status", {"active", "corrected", "superseded", "disputed"})
        domains = meta.get("domains")
        if not isinstance(domains, list) or not domains or any(not isinstance(v, str) or v not in DOMAINS for v in domains):
            errors.append("domains must be a nonempty list of supported domains")
        source = meta.get("source")
        if not isinstance(source, dict):
            errors.append("source must be an object")
        else:
            for field in ("ref", "message_at", "excerpt"):
                if field not in source:
                    errors.append(f"source.{field} is required (null allowed)")
            if source.get("ref") is not None and not _nonempty(source["ref"]):
                errors.append("source.ref must be a nonempty string or null")
            if source.get("message_at") is not None and not _aware(source["message_at"]):
                errors.append("source.message_at must be timezone-aware or null")
            if source.get("excerpt") is not None and not _nonempty(source["excerpt"]):
                errors.append("source.excerpt must be a nonempty string or null")
            if source.get("ref") is None:
                words = body.lower() if isinstance(body, str) else ""
                limitation = ("来源" in words and any(w in words for w in ("未知", "缺失", "无法", "不可"))) or ("source" in words and any(w in words for w in ("unknown", "unavailable", "missing")))
                if not _nonempty(source.get("excerpt")) or not limitation:
                    errors.append("Unknown source requires an excerpt and explicit source limitation in the body")
        time = meta.get("time")
        if not isinstance(time, dict):
            errors.append("time must be an object")
        else:
            for field in ("event", "expressed", "target", "precision", "note"):
                if field not in time:
                    errors.append(f"time.{field} is required (null allowed except precision)")
            precision = time.get("precision")
            if not isinstance(precision, str):
                precision = None
            if precision not in {"instant", "day", "month", "range", "unknown"}:
                errors.append("Invalid time.precision")
            for field in ("event", "expressed", "target"):
                value = time.get(field)
                if value is not None and not _temporal(value):
                    if not (precision in {"range", "unknown"} and _nonempty(value) and _nonempty(time.get("note"))):
                        errors.append(f"time.{field} needs a valid ISO time/date/month, or a documented uncertain range")
            event = time.get("event")
            if event is not None:
                patterns = {"day": r"\d{4}-\d{2}-\d{2}", "month": r"\d{4}-\d{2}"}
                if precision == "instant" and not _aware(event):
                    errors.append("Instant event time must include timezone")
                if precision in patterns and (not isinstance(event, str) or not re.fullmatch(patterns[precision], event)):
                    errors.append("event does not match time.precision")
            if time.get("note") is not None and not _nonempty(time["note"]):
                errors.append("time.note must be a nonempty string or null")
        refs("related")
        if meta.get("kind") == "hypothesis":
            choice("user_endorsement", {"pending", "accepted", "rejected"})
            choice("evidence_state", {"untested", "supported", "mixed", "contradicted", "inconclusive"})
        if meta.get("kind") == "insight":
            if meta.get("author") not in {"user", "ai"}:
                errors.append("insight author must be user or ai")
            if not _nonempty(meta.get("context")):
                errors.append("insight context is required")
            for field in ("valid_from", "valid_until"):
                if field not in meta or (meta[field] is not None and not _temporal(meta[field])):
                    errors.append(f"{field} must be an ISO date/time or null")
            start, end = meta.get("valid_from"), meta.get("valid_until")
            if _aware(start) and _aware(end):
                if datetime.fromisoformat(start.replace("Z", "+00:00")) > datetime.fromisoformat(end.replace("Z", "+00:00")):
                    errors.append("valid_from cannot follow valid_until")
            elif isinstance(start, str) and isinstance(end, str) and len(start) == len(end) and len(start) in {7, 10} and start > end:
                errors.append("valid_from cannot follow valid_until")
            choice("user_endorsement", {"pending", "accepted", "rejected"})
            choice("evidence_state", {"untested", "supported", "mixed", "contradicted", "inconclusive"})
            for field in ("evidence_refs", "counterexample_refs", "revises"):
                refs(field)
            if meta.get("evidence_refs") == []:
                errors.append("insight requires evidence_refs")
            strings("unknowns")
        if meta.get("kind") == "recommendation":
            if meta.get("author") != "ai" or meta.get("basis") != "ai_inference":
                errors.append("recommendation must remain an AI inference")
            for field in ("context", "expected_result"):
                if not _nonempty(meta.get(field)):
                    errors.append(f"recommendation {field} is required")
            choice("adoption_state", {"unknown", "adopted", "declined"})
            adoption_ref = meta.get("adoption_ref")
            if meta.get("adoption_state") == "unknown" and adoption_ref is not None:
                errors.append("unknown adoption cannot have adoption_ref")
            if meta.get("adoption_state") in {"adopted", "declined"} and (not isinstance(adoption_ref, str) or not ID.fullmatch(adoption_ref)):
                errors.append("adopted/declined recommendation requires adoption_ref")
            for field in ("evidence_refs", "counterexample_refs", "process_refs", "outcome_refs", "side_effect_refs", "revision_refs", "revises"):
                refs(field)
            if meta.get("evidence_refs") == []:
                errors.append("recommendation requires evidence_refs")
            strings("unknowns")
    elif document_type == "case":
        choice("kind", {"decision", "state_change", "improvement"})
        choice("status", {"open", "observing", "closed", "paused"})
        for field in ("record_refs", "user_hypothesis_refs", "ai_hypothesis_refs", "outcome_refs"):
            refs(field)
        for field in ("insight_refs", "recommendation_refs"):
            if field in meta:
                refs(field)
        for field in ("purpose", "investment_boundary", "pause_condition"):
            if field in meta and meta[field] is not None and not _nonempty(meta[field]):
                errors.append(f"{field} must be a nonempty string or null")
    elif document_type in {"summary", "report"}:
        choice("kind", {"current" if document_type == "summary" else "periodic"})
        refs("record_refs")
        if meta.get("record_refs") == []:
            errors.append("Summaries and reports require source record_refs")
    elif document_type == "followup":
        if not isinstance(meta.get("case_id"), str) or not ID.fullmatch(meta["case_id"]):
            errors.append("case_id must be a safe ID")
        if not _nonempty(meta.get("question")):
            errors.append("question must be nonempty")
        choice("status", {"active", "paused", "closed"})
        choice("contact_state", {"not_asked", "awaiting_reply", "replied"})
        choice("answer_state", {"unknown", "no_result", "partial", "complete", "unavailable"})
        for field in ("next_check_at", "last_asked_at", "last_answered_at"):
            if field not in meta or (meta[field] is not None and not _aware(meta[field])):
                errors.append(f"{field} must be present and timezone-aware or null")
        if "expected_result_at" not in meta or (meta["expected_result_at"] is not None and not (_aware(meta["expected_result_at"]) or (isinstance(meta["expected_result_at"], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta["expected_result_at"]) and _temporal(meta["expected_result_at"])))):
            errors.append("expected_result_at must be an ISO date, aware timestamp, or null")
        if meta.get("status") == "active" and meta.get("next_check_at") is None:
            errors.append("Active followup requires next_check_at")
        if type(meta.get("cadence_days")) is not int or meta["cadence_days"] != 7:
            errors.append("cadence_days must be 7")
        refs("outcome_refs")
        if "recommendation_refs" in meta:
            refs("recommendation_refs")
        if meta.get("answer_state") in ("partial", "complete") and not meta.get("outcome_refs"):
            errors.append("Partial/complete answer requires outcome_refs")
    return errors


def _root(root):
    path = Path(root).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f"Symlink not allowed: {part}")
    if not path.is_dir():
        raise ValueError(f"Root directory does not exist: {path}")
    return path


def _checked_path(root, relative, personal=False):
    if not isinstance(relative, str) or "\\" in relative:
        raise ValueError("Path must be a relative POSIX string")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or str(pure) != relative or any(p in {".", ".."} for p in pure.parts):
        raise ValueError(f"Unsafe relative path: {relative}")
    if personal:
        _path_type(relative)
    path = root
    for part in pure.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Symlink not allowed: {path}")
    return path


def _snapshot(root):
    documents = {}
    for directory in DIRECTORIES:
        base = _checked_path(root, directory)
        if not base.exists():
            continue
        if not base.is_dir():
            raise ValueError(f"Expected directory: {base}")
        for folder, dirs, files in os.walk(base, followlinks=False):
            for name in dirs + files:
                if (Path(folder) / name).is_symlink():
                    raise ValueError(f"Symlink not allowed: {Path(folder) / name}")
            for name in files:
                if name.endswith(".md"):
                    path = Path(folder) / name
                    relative = path.relative_to(root).as_posix()
                    _checked_path(root, relative, personal=True)
                    documents[relative] = read_document(path)
    return documents


def _validate_snapshot(documents):
    errors, by_id = [], {}
    for path, (meta, body) in documents.items():
        errors.extend(f"{path}: {error}" for error in validate_document(meta, body, path))
        record_id = meta.get("id")
        if isinstance(record_id, str):
            if record_id in by_id:
                errors.append(f"Duplicate global id: {record_id}")
            else:
                by_id[record_id] = meta
    if errors:
        return errors
    for path, (meta, _) in documents.items():
        fields = _reference_fields(meta)
        links = [(field, value) for field in fields for value in meta[field]]
        if meta["document_type"] == "followup":
            links.append(("case_id", meta["case_id"]))
        if meta["document_type"] == "record" and meta.get("kind") == "recommendation" and meta.get("adoption_ref") is not None:
            links.append(("adoption_ref", meta["adoption_ref"]))
        for field, value in links:
            target = by_id.get(value)
            if target is None:
                errors.append(f"{path}: Dangling {field}: {value}")
                continue
            if field == "case_id" and target["document_type"] != "case":
                errors.append(f"{path}: case_id must reference a case")
            if field not in {"related", "case_id"} and target["document_type"] != "record":
                errors.append(f"{path}: {field} must reference base records")
            if field in {"outcome_refs", "side_effect_refs"} and target.get("kind") != "outcome":
                errors.append(f"{path}: outcome_refs must reference outcome records")
            if field in {"evidence_refs", "counterexample_refs"} and target.get("kind") not in {
                    "experience", "observation", "feeling", "view", "intention", "decision", "outcome", "correction"}:
                errors.append(f"{path}: {field} must reference base evidence, not an AI claim")
            if field == "process_refs" and target.get("kind") not in {"experience", "observation", "decision", "outcome"}:
                errors.append(f"{path}: process_refs must reference an observed process")
            if field == "adoption_ref" and (target.get("author") != "user" or target.get("kind") not in {"decision", "outcome"}):
                errors.append(f"{path}: adoption_ref must reference a user decision or outcome")
            if field == "recommendation_refs" and target.get("kind") != "recommendation":
                errors.append(f"{path}: recommendation_refs must reference recommendations")
            if field in {"insight_refs", "revision_refs"} and target.get("kind") != "insight":
                errors.append(f"{path}: {field} must reference insights")
            if field == "revises":
                allowed = {"recommendation"} if meta.get("kind") == "recommendation" else {"insight", "hypothesis"}
                if target.get("kind") not in allowed:
                    errors.append(f"{path}: revises must reference a prior {meta.get('kind')} claim")
                if target.get("status") not in {"corrected", "superseded"}:
                    errors.append(f"{path}: revised record {value} must be corrected or superseded")
            if (field in {"evidence_refs", "counterexample_refs"}
                    and meta.get("status") not in {"corrected", "superseded"}
                    and target.get("status") in {"corrected", "superseded"}):
                errors.append(f"{path}: {field} cannot rely on invalidated record {value}")
            if field in {"user_hypothesis_refs", "ai_hypothesis_refs"}:
                expected_author = "user" if field.startswith("user_") else "ai"
                if target.get("kind") != "hypothesis" or target.get("author") != expected_author:
                    errors.append(f"{path}: {field} must reference {expected_author} hypotheses")
            if meta["document_type"] == "summary" and field == "record_refs":
                if target.get("status") in {"corrected", "superseded"}:
                    errors.append(f"{path}: Current summary references invalidated record {value}")
                if target.get("kind") == "hypothesis" and target.get("user_endorsement") != "accepted":
                    errors.append(f"{path}: Current summary references unaccepted hypothesis {value}")
                if target.get("kind") in {"hypothesis", "insight"} and target.get("evidence_state") == "contradicted":
                    errors.append(f"{path}: Current summary references contradicted claim {value}")
                if target.get("kind") == "insight" and target.get("user_endorsement") != "accepted":
                    errors.append(f"{path}: Current summary references unaccepted insight {value}")
                if target.get("kind") == "recommendation":
                    errors.append(f"{path}: Current summary cannot treat recommendation as personal fact {value}")
    return errors


def _reference_fields(meta):
    fields = {
        "record": ["related"],
        "case": ["record_refs", "user_hypothesis_refs", "ai_hypothesis_refs", "outcome_refs"],
        "followup": ["outcome_refs"],
        "summary": ["record_refs"],
        "report": ["record_refs"],
    }[meta["document_type"]]
    if meta["document_type"] == "record" and meta.get("kind") == "insight":
        fields += ["evidence_refs", "counterexample_refs", "revises"]
    if meta["document_type"] == "record" and meta.get("kind") == "recommendation":
        fields += ["evidence_refs", "counterexample_refs", "process_refs", "outcome_refs", "side_effect_refs", "revision_refs", "revises"]
    if meta["document_type"] == "case":
        fields += [field for field in ("insight_refs", "recommendation_refs") if field in meta]
    if meta["document_type"] == "followup" and "recommendation_refs" in meta:
        fields.append("recommendation_refs")
    return fields


def _impact_from_snapshot(snapshot, record_id):
    matches = [(path, meta) for path, (meta, _) in snapshot.items() if meta.get("id") == record_id]
    if not matches:
        raise ValueError(f"Unknown record ID: {record_id}")
    target_path, target = matches[0]
    references, review_required = [], []
    for path, (meta, body) in sorted(snapshot.items()):
        if path == target_path:
            continue
        fields = [field for field in _reference_fields(meta) if record_id in meta[field]]
        if meta.get("case_id") == record_id:
            fields.append("case_id")
        if meta.get("adoption_ref") == record_id:
            fields.append("adoption_ref")
        body_mention = record_id in body
        if fields or body_mention:
            item = {"path": path, "document_type": meta["document_type"],
                    "kind": meta.get("kind"), "fields": fields, "body_mention": body_mention}
            references.append(item)
            if not (meta.get("kind") == "correction" and fields == ["related"] and not body_mention):
                review_required.append(item)
    return {"record_id": record_id, "status": target.get("status"), "references": references,
            "review_required": review_required if target.get("status") in {"corrected", "superseded"} else [],
            "limitation": "Reference and exact-text scan only; inspect meaning, paraphrases and external outputs manually."}


def impact(root, record_id):
    if not isinstance(record_id, str) or not ID.fullmatch(record_id):
        raise ValueError("record_id must be a safe ID")
    root = _root(root)
    with _lock(root):
        _require_recovered(root)
        snapshot = _snapshot(root)
    errors = _validate_snapshot(snapshot)
    if errors:
        raise ValueError("\n".join(errors))
    return _impact_from_snapshot(snapshot, record_id)


def validate_root(root):
    try:
        root = _root(root)
        with _lock(root):
            _require_recovered(root)
            return _validate_snapshot(_snapshot(root))
    except (OSError, ValueError, yaml.YAMLError, TypeError) as exc:
        return [str(exc)]


@contextmanager
def _lock(root):
    import fcntl
    path = _checked_path(root, "operations/.writer.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".personal-os-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _journal_path(root, operation_id):
    if not isinstance(operation_id, str) or not ID.fullmatch(operation_id):
        raise ValueError("operation_id must be a safe lowercase slug")
    return _checked_path(root, f"operations/changes/{operation_id}.json")


def _require_recovered(root, except_operation=None):
    """Prevent readers and new writers from treating a partial bundle as final."""
    directory = _checked_path(root, "operations/changes")
    if not directory.exists():
        return
    for path in directory.glob("*.json"):
        _checked_path(root, path.relative_to(root).as_posix())
        journal = json.loads(path.read_text(encoding="utf-8"))
        if journal.get("state") == "prepared" and path.stem != except_operation:
            raise ValueError(f"unfinished operation must be recovered first: {path.stem}")


def get_operation_bundle(root, operation_id):
    """Recover stable input before a higher-level action regenerates timestamps."""
    path = _journal_path(_root(root), operation_id)
    return json.loads(path.read_text(encoding="utf-8"))["bundle"] if path.exists() else None


def get_operation_receipt(root, operation_id):
    path = _journal_path(_root(root), operation_id)
    if not path.exists():
        return None
    journal = json.loads(path.read_text(encoding="utf-8"))
    return journal.get("receipt") if journal.get("state") == "completed" else None


def _prepare(root, bundle, operation_id, fingerprint):
    if not isinstance(bundle, dict) or not isinstance(bundle.get("changes"), list) or not bundle["changes"]:
        raise ValueError("Bundle must contain a nonempty changes list")
    snapshot = _snapshot(root)
    writes, seen = [], set()
    timestamp = _now()
    for change in bundle["changes"]:
        if not isinstance(change, dict) or not all(f in change for f in ("path", "metadata", "body", "expected_sha256")):
            raise ValueError("Every change requires path, metadata, body and expected_sha256")
        relative = change["path"]
        path = _checked_path(root, relative, personal=True)
        if relative in seen:
            raise ValueError(f"Duplicate change path: {relative}")
        seen.add(relative)
        old = path.read_bytes() if path.exists() else None
        old_hash = _hash(old) if old is not None else None
        expected = change["expected_sha256"]
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)):
            raise ValueError("expected_sha256 must be null or a lowercase SHA256")
        if old_hash != expected:
            raise ValueError(f"Concurrent modification / expected_sha256 mismatch: {relative}")
        meta = dict(change["metadata"]) if isinstance(change["metadata"], dict) else None
        if meta is None:
            raise ValueError("metadata must be an object")
        if old is not None:
            original_meta = snapshot[relative][0]
            if meta.get("id") != original_meta.get("id") or meta.get("document_type") != original_meta.get("document_type"):
                raise ValueError("Cannot change a document's id or document_type")
            if original_meta.get("kind") in {"insight", "recommendation"}:
                if meta.get("kind") != original_meta["kind"]:
                    raise ValueError("Cannot change an insight or recommendation kind")
                if original_meta["kind"] == "recommendation":
                    for field in ("context", "expected_result", "evidence_refs"):
                        if meta.get(field) != original_meta.get(field):
                            raise ValueError(f"Original recommendation {field} cannot change; create a revision")
                    old_adoption = original_meta.get("adoption_ref")
                    if old_adoption and meta.get("adoption_ref") != old_adoption and old_adoption not in meta.get("related", []):
                        raise ValueError("Keep the previous adoption_ref in related when adoption changes")
            meta["recorded_at"] = original_meta.get("recorded_at")
        else:
            meta["recorded_at"] = timestamp
        meta["updated_at"] = timestamp
        snapshot[relative] = (meta, change["body"])
        content = render_document(meta, change["body"])
        writes.append({"path": relative, "old_sha256": old_hash, "new_sha256": _hash(content.encode("utf-8")), "content": content})
    errors = _validate_snapshot(snapshot)
    if errors:
        raise ValueError("Bundle validation failed:\n" + "\n".join(errors))
    return {"schema_version": 1, "operation_id": operation_id, "input_hash": fingerprint, "bundle": bundle, "state": "prepared", "prepared_at": timestamp, "writes": writes}


def _recover(root, journal):
    snapshot = _snapshot(root)
    for write in journal["writes"]:
        path = _checked_path(root, write["path"], personal=True)
        content = write["content"]
        if _hash(content.encode("utf-8")) != write["new_sha256"]:
            raise ValueError("Corrupted operation journal content")
        current_hash = _hash(path.read_bytes()) if path.exists() else None
        if current_hash not in {write["old_sha256"], write["new_sha256"]}:
            raise ValueError(f"Recovery blocked by unrelated edit: {write['path']}")
        match = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", content, re.S)
        if not match:
            raise ValueError("Corrupted journal Markdown")
        snapshot[write["path"]] = (yaml.load(match.group(1), Loader=StrictLoader), match.group(2).strip())
    errors = _validate_snapshot(snapshot)
    if errors:
        raise ValueError("Recovery validation failed:\n" + "\n".join(errors))
    for write in journal["writes"]:
        path = _checked_path(root, write["path"], personal=True)
        current_hash = _hash(path.read_bytes()) if path.exists() else None
        if current_hash == write["new_sha256"]:
            continue
        if current_hash != write["old_sha256"]:
            raise ValueError(f"Concurrent modification during recovery: {write['path']}")
        _atomic_write(path, write["content"].encode("utf-8"))
    for write in journal["writes"]:
        if _hash(_checked_path(root, write["path"], personal=True).read_bytes()) != write["new_sha256"]:
            raise ValueError(f"Local read-back failed: {write['path']}")


def apply_bundle(root, bundle, operation_id):
    root = _root(root)
    journal_path = _journal_path(root, operation_id)
    fingerprint = _hash(_json(bundle).encode("utf-8"))
    with _lock(root):
        _require_recovered(root, except_operation=operation_id)
        if journal_path.exists():
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            if journal.get("input_hash") != fingerprint:
                raise ValueError("operation_id already belongs to different input")
            if journal.get("state") == "completed":
                return {**journal["receipt"], "idempotent_replay": True}
            if journal.get("state") != "prepared":
                raise ValueError("Invalid journal state")
        else:
            journal = _prepare(root, bundle, operation_id, fingerprint)
            _atomic_write(journal_path, (_json(journal) + "\n").encode("utf-8"))
        _recover(root, journal)
        receipt = {
            "operation_id": operation_id, "status": "local_saved", "remote_verified": False,
            "local_verified_at": _now(), "idempotent_replay": False,
            "files": [{"path": w["path"], "sha256": w["new_sha256"]} for w in journal["writes"]],
        }
        invalidated = []
        for write in journal["writes"]:
            meta, _ = read_document(_checked_path(root, write["path"], personal=True))
            if meta.get("document_type") == "record" and meta.get("status") in {"corrected", "superseded"}:
                invalidated.append(meta["id"])
        if invalidated:
            snapshot = _snapshot(root)
            receipt["review_required"] = [_impact_from_snapshot(snapshot, record_id)
                                          for record_id in invalidated]
        journal.update(state="completed", receipt=receipt)
        _atomic_write(journal_path, (_json(journal) + "\n").encode("utf-8"))
        return receipt


def build_index(root):
    root = _root(root)
    with _lock(root):
        _require_recovered(root)
        snapshot = _snapshot(root)
        errors = _validate_snapshot(snapshot)
        if errors:
            raise ValueError("\n".join(errors))
        result = {"indexed_at": _now(), "search_method": "keyword", "documents": [{"path": path, "metadata": meta, "body": body} for path, (meta, body) in sorted(snapshot.items())]}
        path = _checked_path(root, "generated/index/records.json")
        _atomic_write(path, (_json(result) + "\n").encode("utf-8"))
        return {"status": "index_saved", "path": str(path), "count": len(snapshot), "search_method": "keyword"}


def search(root, query, include_history=False):
    if not _nonempty(query):
        raise ValueError("query must be nonempty")
    root = _root(root)
    with _lock(root):
        _require_recovered(root)
        snapshot = _snapshot(root)
    errors = _validate_snapshot(snapshot)
    if errors:
        raise ValueError("\n".join(errors))
    normalize = lambda text: " ".join(unicodedata.normalize("NFKC", text).casefold().split())
    phrase = normalize(query)
    terms = phrase.split()
    results = []
    for path, (meta, body) in snapshot.items():
        if not include_history and meta.get("status") in {"corrected", "superseded"}:
            continue
        haystack = normalize(_json(meta) + " " + body)
        matches = [term for term in terms if term in haystack]
        if phrase in haystack or matches:
            score = (100 if phrase in haystack else 0) + len(matches)
            lines = [line.strip() for line in body.splitlines() if line.strip() and not line.startswith("#")]
            excerpt = next((line for line in lines if any(term in normalize(line) for term in terms)), body.strip())[:300]
            results.append({"path": path, "id": meta["id"], "document_type": meta["document_type"], "status": meta.get("status"), "kind": meta.get("kind"), "author": meta.get("author"), "user_endorsement": meta.get("user_endorsement"), "evidence_state": meta.get("evidence_state"), "matched_terms": matches, "excerpt": excerpt, "score": score})
    results.sort(key=lambda item: (-item["score"], item["path"]))
    return {"search_method": "keyword", "include_history": include_history, "query": query, "count": len(results), "results": results, "limitation": "Keyword matches only; no match does not prove an event never happened. Read original records and their links before reasoning."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "apply", "index", "search", "impact"):
        command = sub.add_parser(name)
        command.add_argument("--root", required=True)
        if name == "apply":
            command.add_argument("--bundle", required=True)
            command.add_argument("--operation-id", required=True)
        elif name == "search":
            command.add_argument("--query", required=True)
            command.add_argument("--include-history", action="store_true")
        elif name == "impact":
            command.add_argument("--record-id", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            errors = validate_root(args.root)
            result = {"ok": not errors, "errors": errors}
        elif args.command == "apply":
            bundle = json.loads(Path(args.bundle).read_text(encoding="utf-8"))
            result = {"ok": True, **apply_bundle(args.root, bundle, args.operation_id)}
        elif args.command == "index":
            result = {"ok": True, **build_index(args.root)}
        elif args.command == "search":
            result = {"ok": True, **search(args.root, args.query, args.include_history)}
        else:
            result = {"ok": True, **impact(args.root, args.record_id)}
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
