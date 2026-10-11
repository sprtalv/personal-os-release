#!/usr/bin/env python3
"""Publish explicitly selected local-save operations and verify a remote commit.

Requires a configured, existing remote branch. Does not bootstrap repositories,
merge, force push, or publish unrelated local commits. Intended for the single
Linux cloud writer. No network is performed by --inspect.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import records


def git(root, *args, check=True):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=60)
    if check and result.returncode:
        # Remote configuration can contain credentials: never echo stderr/URLs.
        raise ValueError(f"Git {args[0]} failed (exit {result.returncode}); inspect authentication, branch or conflicts without exposing credentials")
    return result


def text(root, *args):
    return git(root, *args).stdout.decode("utf-8").strip()


def configuration(root):
    config = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    repo = config.get("repository", {})
    remote, branch = repo.get("remote"), repo.get("branch")
    if not remote or not branch:
        raise ValueError("repository.remote and repository.branch are not configured")
    if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote):
        raise ValueError("remote must be a configured Git remote name")
    if not isinstance(branch, str) or git(root, "check-ref-format", "refs/heads/" + branch, check=False).returncode:
        raise ValueError("invalid branch")
    if remote not in text(root, "remote").splitlines():
        raise ValueError("configured remote does not exist")
    if Path(text(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("root must be the repository top-level directory")
    if text(root, "symbolic-ref", "--short", "HEAD") != branch:
        raise ValueError("checkout must already be on the configured formal branch")
    return remote, branch


def _save(path, value):
    records._atomic_write(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode())


def selected_files(root, operation_ids):
    chosen = {}
    for op in operation_ids:
        receipt = records.get_operation_receipt(root, op)
        if not receipt or receipt.get("status") != "local_saved":
            raise ValueError(f"operation is not locally completed: {op}")
        for item in receipt["files"]:
            records._checked_path(root, item["path"], personal=True)
            chosen[item["path"]] = item["sha256"]
    if not chosen:
        raise ValueError("no files selected")
    return chosen


def _fetch(root, remote, branch):
    ref = f"refs/remotes/{remote}/{branch}"
    git(root, "fetch", "--no-tags", remote, f"+refs/heads/{branch}:{ref}")
    return ref, text(root, "rev-parse", ref)


def _blob_matches(root, commit, files):
    for relative, expected in files.items():
        value = git(root, "show", f"{commit}:{relative}", check=False)
        if value.returncode or hashlib.sha256(value.stdout).hexdigest() != expected:
            return False
    return True


def _commit_matches(root, commit, journal):
    parent = git(root, "rev-parse", f"{commit}^", check=False)
    if parent.returncode or parent.stdout.decode().strip() != journal["base"]:
        return False
    names = set(text(root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines())
    return names == set(journal["files"]) and _blob_matches(root, commit, journal["files"])


def publish(root, operation_ids, publish_id, execute=False):
    root = Path(root).resolve()
    if not records.ID.fullmatch(publish_id):
        raise ValueError("publish_id must be a safe slug")
    remote, branch = configuration(root)
    if not execute:
        return {"status": "ready_for_explicit_publish", "remote_verified": False,
                "remote": remote, "branch": branch, "files": selected_files(root, operation_ids),
                "note": "No network or git writes performed"}
    journal_path = records._checked_path(root, f"operations/publish/{publish_id}.json")
    with records._lock(root):
        for pending in (root / "operations" / "changes").glob("*.json"):
            if pending.is_symlink():
                raise ValueError("symlinked operation journal is not supported")
            if json.loads(pending.read_text(encoding="utf-8")).get("state") == "prepared":
                raise ValueError("unfinished local operation must be recovered before publishing")
        files = selected_files(root, operation_ids)
        request = {"operation_ids": operation_ids, "remote": remote, "branch": branch, "files": files}
        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        journal = json.loads(journal_path.read_text()) if journal_path.exists() else None
        if journal and journal.get("fingerprint") != fingerprint:
            raise ValueError("publish_id already belongs to a different request")
        remote_ref, remote_head = _fetch(root, remote, branch)
        head = text(root, "rev-parse", "HEAD")
        if journal is None:
            if head != remote_head:
                raise ValueError("local HEAD differs from formal remote branch; reconcile explicitly before publishing")
            if text(root, "diff", "--cached", "--name-only"):
                raise ValueError("Git index has pre-existing staged changes")
            # Already holding the shared writer lock; do not reacquire flock.
            errors = records._validate_snapshot(records._snapshot(root))
            if errors:
                raise ValueError("record validation failed: " + "; ".join(errors))
            # Include every changed personal file, so cross-file repairs never
            # become half-published. Other workspace files may remain dirty.
            changed = set(text(root, "diff", "--name-only", "HEAD", "--", "data", "reports").splitlines())
            changed.update(text(root, "ls-files", "--others", "--exclude-standard", "--", "data", "reports").splitlines())
            if not changed or changed != set(files):
                raise ValueError("selected operations must cover exactly all changed personal files; include related operation IDs in order")
            for relative, sha in files.items():
                if hashlib.sha256(records._checked_path(root, relative, personal=True).read_bytes()).hexdigest() != sha:
                    raise ValueError(f"selected receipt is stale: {relative}")
            journal = {**request, "fingerprint": fingerprint, "publish_id": publish_id,
                       "state": "prepared", "base": head, "commit": None}
            _save(journal_path, journal)

        if journal["commit"] is None:
            if head != journal["base"]:
                # Recover the narrow crash window after commit, before logging.
                if not _commit_matches(root, head, journal):
                    raise ValueError("unexpected local commit during publish recovery")
                journal.update(commit=head, state="committed")
                _save(journal_path, journal)
            else:
                if remote_head != journal["base"]:
                    raise ValueError("remote changed before commit; reconcile without force push")
                staged = set(text(root, "diff", "--cached", "--name-only").splitlines())
                if not staged.issubset(files):
                    raise ValueError("unrelated staged files block publish recovery")
                for relative, sha in files.items():
                    if hashlib.sha256(records._checked_path(root, relative, personal=True).read_bytes()).hexdigest() != sha:
                        raise ValueError("personal file changed after publish preparation")
                git(root, "add", "--", *files)
                if set(text(root, "diff", "--cached", "--name-only").splitlines()) != set(files):
                    raise ValueError("unexpected index contents")
                for relative, sha in files.items():
                    if hashlib.sha256(git(root, "show", f":{relative}").stdout).hexdigest() != sha:
                        raise ValueError("staged content differs from prepared record")
                git(root, "commit", "-m", f"Personal OS: archive {publish_id}")
                commit = text(root, "rev-parse", "HEAD")
                if not _commit_matches(root, commit, journal):
                    raise ValueError("commit differs from selected records; do not push")
                journal.update(commit=commit, state="committed")
                _save(journal_path, journal)

        commit = journal["commit"]
        included = git(root, "merge-base", "--is-ancestor", commit, remote_ref, check=False).returncode == 0
        if not included:
            if remote_head != journal["base"]:
                raise ValueError("remote advanced; reconcile without force push")
            # Push the exact recorded commit, never an unrelated newer HEAD.
            git(root, "push", remote, f"{commit}:refs/heads/{branch}")
            remote_ref, remote_head = _fetch(root, remote, branch)
            if git(root, "merge-base", "--is-ancestor", commit, remote_ref, check=False).returncode:
                raise ValueError("published commit not found on formal remote branch")
        if not _blob_matches(root, commit, files):
            raise ValueError("remote-reachable commit content failed read-back")
        receipt = {"status": "remote_verified", "remote_verified": True,
                   "publish_id": publish_id, "commit": commit, "remote_head": remote_head,
                   "verification_scope": "committed_version_on_formal_branch",
                   "current_head_matches": _blob_matches(root, remote_head, files),
                   "verified_at": records._now(), "files": files}
        journal.update(state="remote_verified", receipt=receipt)
        _save(journal_path, journal)
        return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--operation-id", action="append", required=True)
    parser.add_argument("--publish-id", required=True)
    parser.add_argument("--execute", action="store_true", help="Perform configured Git commit/push/read-back; omit for local inspection")
    args = parser.parse_args(argv)
    try:
        result = publish(args.root, args.operation_id, args.publish_id, args.execute)
    except (ValueError, OSError, subprocess.SubprocessError, KeyError) as exc:
        print(json.dumps({"status": "blocked_or_failed", "remote_verified": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
