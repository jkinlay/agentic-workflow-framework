import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic.publication import render_scan, scan_repository


def git(repository, *args):
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-C", str(repository), *args],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def placeholder_locator():
    return "\\" * 2 + "example.invalid" + "\\" + "share" + "\\" + "item"


class Repository:
    def __init__(self, parent):
        self.path = Path(parent) / "repository"
        self.path.mkdir()
        git(self.path, "init", "-b", "main")
        git(self.path, "config", "user.name", "Synthetic User")
        git(self.path, "config", "user.email", "synthetic@example.invalid")
        self.write("context.txt", "locator=" + placeholder_locator() + "\nretained\n")
        self.write(".gitignore", ".agentic-state/publication-deny.json\n")
        git(self.path, "add", "context.txt", ".gitignore")
        git(self.path, "commit", "-m", "common base")
        git(self.path, "branch", "feature")

    def write(self, relative, text):
        path = self.path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message, *paths):
        git(self.path, "add", "--", *paths)
        git(self.path, "commit", "-m", message)
        return git(self.path, "rev-parse", "HEAD")

    def mapping(self):
        path = self.path / ".agentic-state" / "publication-deny.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"version": 1, "aliases": {"placeholder": [placeholder_locator()]}}),
            encoding="utf-8",
        )
        return path


class PublicationSyncMergeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-publication-sync-history-")

    def tearDown(self):
        self.temp.cleanup()

    def test_deletion_from_main_in_earlier_of_two_sync_merges_is_pre_existing(self):
        repo = Repository(self.temp.name)
        mapping = repo.mapping()
        repo.write("context.txt", "retained\n")
        repo.commit("main removes placeholder", "context.txt")

        git(repo.path, "switch", "feature")
        repo.write("feature.txt", "first feature change\n")
        repo.commit("first feature change", "feature.txt")
        git(repo.path, "merge", "--no-ff", "main", "-m", "first sync from main")
        earlier_sync = git(repo.path, "rev-parse", "HEAD")

        git(repo.path, "switch", "main")
        repo.write("main.txt", "later main change\n")
        base = repo.commit("later main change", "main.txt")
        git(repo.path, "switch", "feature")
        repo.write("feature.txt", "first feature change\nsecond feature change\n")
        repo.commit("second feature change", "feature.txt")
        git(repo.path, "merge", "--no-ff", "main", "-m", "second sync from main")
        head = git(repo.path, "rev-parse", "HEAD")

        result = scan_repository(repo.path, base, head, mapping_path=mapping)

        deleted = [
            item
            for item in result["findings"]
            if item["commit"] == earlier_sync
            and item["path"] == "context.txt"
            and item["change"] == "deleted"
        ]
        self.assertTrue(deleted, render_scan(result))
        self.assertTrue(
            all(item["classification"] == "PRE_EXISTING" for item in deleted),
            render_scan(result),
        )
        self.assertEqual("PASS", result["status"], render_scan(result))

    def test_pr_authored_non_merge_deletion_stays_blocking(self):
        repo = Repository(self.temp.name)
        mapping = repo.mapping()
        git(repo.path, "switch", "feature")
        repo.write("context.txt", "retained\n")
        pr_deletion = repo.commit("feature removes placeholder", "context.txt")

        git(repo.path, "switch", "main")
        repo.write("context.txt", "retained\n")
        repo.write("main.txt", "main change\n")
        base = repo.commit("main also removes placeholder", "context.txt", "main.txt")
        git(repo.path, "switch", "feature")
        git(repo.path, "merge", "--no-ff", "main", "-m", "sync from main")
        head = git(repo.path, "rev-parse", "HEAD")

        result = scan_repository(repo.path, base, head, mapping_path=mapping)

        deleted = [
            item
            for item in result["findings"]
            if item["commit"] == pr_deletion
            and item["path"] == "context.txt"
            and item["change"] == "deleted"
        ]
        self.assertTrue(deleted, render_scan(result))
        self.assertTrue(
            all(item["classification"] == "BLOCKING" for item in deleted),
            render_scan(result),
        )
        self.assertEqual("BLOCKED", result["status"], render_scan(result))

    def test_merge_with_second_parent_not_reachable_from_main_stays_blocking(self):
        repo = Repository(self.temp.name)
        mapping = repo.mapping()
        git(repo.path, "branch", "side")

        repo.write("context.txt", "retained\n")
        repo.write("main.txt", "main change\n")
        base = repo.commit("main removes placeholder", "context.txt", "main.txt")
        git(repo.path, "switch", "feature")
        git(repo.path, "merge", "--no-ff", "main", "-m", "sync from main")
        repo.write("context.txt", "locator=" + placeholder_locator() + "\nretained\n")
        repo.write("feature.txt", "feature change\n")
        repo.commit("feature restores placeholder", "context.txt", "feature.txt")

        git(repo.path, "switch", "side")
        repo.write("context.txt", "retained\n")
        repo.commit("side removes placeholder", "context.txt")
        git(repo.path, "switch", "feature")
        git(repo.path, "merge", "--no-ff", "side", "-m", "merge side")
        side_merge = git(repo.path, "rev-parse", "HEAD")

        result = scan_repository(repo.path, base, side_merge, mapping_path=mapping)

        deleted = [
            item
            for item in result["findings"]
            if item["commit"] == side_merge
            and item["path"] == "context.txt"
            and item["change"] == "deleted"
        ]
        self.assertTrue(deleted, render_scan(result))
        self.assertTrue(
            all(item["classification"] == "BLOCKING" for item in deleted),
            render_scan(result),
        )
        self.assertEqual("BLOCKED", result["status"], render_scan(result))


if __name__ == "__main__":
    unittest.main()
