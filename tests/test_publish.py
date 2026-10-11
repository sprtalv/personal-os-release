"""Publishing integration tests use local bare repositories, never real remotes."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / ".agents/skills/personal-os/scripts"
sys.path.insert(0, str(SCRIPTS))
import publish
from test_records import base_record, change


def git_at(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(f"Git {args} failed: {result.stderr}")
    return result.stdout.strip()


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.remote = self.base / "remote.git"
        self.root = self.base / "work"
        self.remote.mkdir()
        self.root.mkdir()
        git_at(self.remote, "init", "--bare", "--initial-branch=main")
        git_at(self.root, "init", "--initial-branch=main")
        git_at(self.root, "config", "user.name", "Personal OS Test")
        git_at(self.root, "config", "user.email", "personal-os-test@example.invalid")
        self.config = {"repository": {"remote": "origin", "branch": "main"}}
        (self.root / "settings.json").write_text(json.dumps(self.config))
        (self.root / ".gitignore").write_text("operations/\ngenerated/\n__pycache__/\n")
        git_at(self.root, "add", "settings.json", ".gitignore")
        git_at(self.root, "commit", "-m", "initial configuration")
        git_at(self.root, "remote", "add", "origin", str(self.remote))
        git_at(self.root, "push", "-u", "origin", "main")
        self.initial = git_at(self.root, "rev-parse", "HEAD")

    def tearDown(self):
        self.temporary.cleanup()

    def save(self, record_id="event-one", operation_id="save-one"):
        item = change(base_record(record_id))
        publish.records.apply_bundle(self.root, {"changes": [item]}, operation_id)
        return item

    def publish(self, operations=None, publish_id="publish-one"):
        return publish.publish(self.root, operations or ["save-one"], publish_id, execute=True)

    def remote_head(self):
        return git_at(self.remote, "rev-parse", "refs/heads/main")

    def test_publish_verified_commit_and_local_bare_remote_readback(self):
        item = self.save()
        receipt = self.publish()
        self.assertEqual(receipt["status"], "remote_verified")
        self.assertTrue(receipt["remote_verified"])
        self.assertTrue(receipt["current_head_matches"])
        self.assertEqual(receipt["commit"], self.remote_head())
        expected = (self.root / item["path"]).read_text().strip()
        self.assertEqual(git_at(self.remote, "show", "main:" + item["path"]), expected)
        self.assertEqual(set(git_at(self.root, "diff-tree", "--no-commit-id", "--name-only", "-r", receipt["commit"]).splitlines()), {item["path"]})
        journal = json.loads((self.root / "operations/publish/publish-one.json").read_text())
        self.assertEqual(journal["state"], "remote_verified")

    def test_inspection_performs_no_fetch_commit_or_push(self):
        self.save()
        with patch.object(publish, "_fetch", side_effect=AssertionError("Inspection must not fetch")):
            receipt = publish.publish(self.root, ["save-one"], "inspect-one", execute=False)
        self.assertFalse(receipt["remote_verified"])
        self.assertEqual(git_at(self.root, "rev-parse", "HEAD"), self.initial)
        self.assertEqual(self.remote_head(), self.initial)
        self.assertFalse((self.root / "operations/publish/inspect-one.json").exists())

    def test_completed_retry_rechecks_remote_without_duplicate_commit_or_push(self):
        self.save()
        first = self.publish()
        real_git = publish.git
        with patch.object(publish, "git", wraps=real_git) as calls:
            second = self.publish()
        self.assertEqual(first["commit"], second["commit"])
        self.assertEqual(git_at(self.root, "rev-list", "--count", "HEAD"), "2")
        self.assertFalse(any(call.args[1] in {"commit", "push"} for call in calls.call_args_list))

    def test_recovers_crash_after_commit_before_journal_save(self):
        self.save()
        actual_save = publish._save
        def stop_after_commit(path, journal):
            if journal.get("state") == "committed":
                raise OSError("simulated crash after commit")
            return actual_save(path, journal)
        with patch.object(publish, "_save", side_effect=stop_after_commit):
            with self.assertRaisesRegex(OSError, "after commit"):
                self.publish()
        committed = git_at(self.root, "rev-parse", "HEAD")
        self.assertNotEqual(committed, self.initial)
        self.assertEqual(self.remote_head(), self.initial)
        journal = json.loads((self.root / "operations/publish/publish-one.json").read_text())
        self.assertEqual(journal["state"], "prepared")
        self.assertIsNone(journal["commit"])
        recovered = self.publish()
        self.assertEqual(recovered["commit"], committed)
        self.assertEqual(self.remote_head(), committed)
        self.assertEqual(git_at(self.root, "rev-list", "--count", "HEAD"), "2")

    def test_recovers_crash_after_push_before_readback(self):
        self.save()
        actual_fetch = publish._fetch
        fetches = 0
        def stop_second_fetch(*args):
            nonlocal fetches
            fetches += 1
            if fetches == 2:
                raise OSError("simulated outage after push")
            return actual_fetch(*args)
        with patch.object(publish, "_fetch", side_effect=stop_second_fetch):
            with self.assertRaisesRegex(OSError, "after push"):
                self.publish()
        committed = git_at(self.root, "rev-parse", "HEAD")
        self.assertEqual(self.remote_head(), committed)
        journal = json.loads((self.root / "operations/publish/publish-one.json").read_text())
        self.assertEqual(journal["state"], "committed")
        real_git = publish.git
        with patch.object(publish, "git", wraps=real_git) as calls:
            receipt = self.publish()
        self.assertEqual(receipt["commit"], committed)
        self.assertTrue(receipt["remote_verified"])
        self.assertFalse(any(call.args[1] == "push" for call in calls.call_args_list))

    def test_rejects_unconfigured_remote_and_wrong_branch(self):
        self.save()
        (self.root / "settings.json").write_text(json.dumps({"repository": {"remote": None, "branch": None}}))
        with self.assertRaisesRegex(ValueError, "not configured"):
            self.publish()
        (self.root / "settings.json").write_text(json.dumps(self.config))
        git_at(self.root, "checkout", "-b", "different-branch")
        with self.assertRaisesRegex(ValueError, "configured formal branch"):
            self.publish()
        self.assertEqual(self.remote_head(), self.initial)

    def test_rejects_preexisting_staged_unrelated_file(self):
        self.save()
        (self.root / "unrelated.txt").write_text("other work")
        git_at(self.root, "add", "unrelated.txt")
        with self.assertRaisesRegex(ValueError, "pre-existing staged"):
            self.publish()
        self.assertEqual(git_at(self.root, "diff", "--cached", "--name-only"), "unrelated.txt")
        self.assertEqual(self.remote_head(), self.initial)

    def test_rejects_unpushed_unrelated_commit(self):
        self.save()
        (self.root / "unrelated.txt").write_text("other work")
        git_at(self.root, "add", "unrelated.txt")
        git_at(self.root, "commit", "-m", "unrelated unpushed change")
        local = git_at(self.root, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ValueError, "HEAD differs"):
            self.publish()
        self.assertEqual(git_at(self.root, "rev-parse", "HEAD"), local)
        self.assertEqual(self.remote_head(), self.initial)

    def test_rejects_stale_selected_receipt(self):
        item = self.save()
        path = self.root / item["path"]
        path.write_text(path.read_text() + "\n后来的独立编辑。\n")
        with self.assertRaisesRegex(ValueError, "receipt is stale"):
            self.publish()
        self.assertEqual(self.remote_head(), self.initial)

    def test_all_changed_personal_files_must_be_selected_together(self):
        self.save()
        related = change(base_record("event-two", related=["event-one"]))
        publish.records.apply_bundle(self.root, {"changes": [related]}, "save-two")
        with self.assertRaisesRegex(ValueError, "exactly all changed personal files"):
            self.publish()
        self.assertEqual(self.remote_head(), self.initial)
        receipt = self.publish(["save-one", "save-two"])
        self.assertEqual(len(receipt["files"]), 2)
        self.assertEqual(self.remote_head(), receipt["commit"])

    def test_remote_advancement_after_local_commit_blocks_without_force_push(self):
        self.save()
        actual_save = publish._save
        def stop_after_commit(path, journal):
            if journal.get("state") == "committed":
                raise OSError("simulated crash")
            return actual_save(path, journal)
        with patch.object(publish, "_save", side_effect=stop_after_commit):
            with self.assertRaises(OSError):
                self.publish()
        committed = git_at(self.root, "rev-parse", "HEAD")
        other = self.base / "other-work"
        git_at(self.base, "clone", str(self.remote), str(other))
        git_at(other, "config", "user.name", "Other Test")
        git_at(other, "config", "user.email", "other-test@example.invalid")
        (other / "other.txt").write_text("remote independently advances")
        git_at(other, "add", "other.txt")
        git_at(other, "commit", "-m", "independent remote change")
        git_at(other, "push", "origin", "main")
        remote_advanced = self.remote_head()
        real_git = publish.git
        with patch.object(publish, "git", wraps=real_git) as calls:
            with self.assertRaisesRegex(ValueError, "remote advanced"):
                self.publish()
        self.assertEqual(self.remote_head(), remote_advanced)
        self.assertEqual(git_at(self.root, "rev-parse", "HEAD"), committed)
        self.assertFalse(any(call.args[1] == "push" for call in calls.call_args_list))

    def test_prepared_local_record_operation_blocks_publish(self):
        self.save()
        path = self.root / "operations/changes/interrupted-operation.json"
        path.write_text(json.dumps({"state": "prepared", "operation_id": "interrupted-operation"}))
        with self.assertRaisesRegex(ValueError, "prepared|unfinished|incomplete"):
            self.publish()
        self.assertEqual(self.remote_head(), self.initial)


if __name__ == "__main__":
    unittest.main()
