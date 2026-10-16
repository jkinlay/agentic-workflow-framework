"""Byte-exact ``check_commit_range`` acceptance tests for AWF-95.

Every fixture repository lives under the platform temporary directory and is
created with Git plumbing.  The tests deliberately avoid checkout-dependent
pathname conversion so the same raw object bytes are exercised on every host.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError  # noqa: E402
import agentic.provenance as provenance  # noqa: E402


RUN_ID = "11111111-1111-4111-8111-111111111111"


class _RepositoryCase(unittest.TestCase):
    object_format = "sha1"

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="awf-range-bytes-")
        self.scratch = Path(self.temporary.name)
        self.root = self.scratch / "repo"
        self.environment = {
            **os.environ,
            "GIT_AUTHOR_NAME": "Fixture Writer",
            "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "Fixture Writer",
            "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        }
        arguments = ["init", "--quiet", "--initial-branch=main"]
        if self.object_format != "sha1":
            arguments.append(f"--object-format={self.object_format}")
        initialized = subprocess.run(
            ["git", *arguments, str(self.root)], capture_output=True,
            env=self.environment, timeout=30,
        )
        if initialized.returncode:
            if self.object_format == "sha256":
                self.skipTest(
                    "local Git lacks SHA-256 object format support: "
                    + initialized.stderr.decode("utf-8", "replace").strip()
                )
            self.fail(initialized.stderr.decode("utf-8", "replace"))
        self.empty_tree = self.mktree([])
        self.base = self.commit(self.empty_tree, None, self.message("base"))
        self.git("update-ref", "refs/heads/main", self.base)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def message(subject="change", *, writer=f"codex/{RUN_ID}"):
        return f"{subject}\n\nWriter: {writer}".encode("utf-8")

    def git(self, *arguments, input_bytes=None, allowed=(0,)):
        result = subprocess.run(
            ["git", "-C", str(self.root), *arguments], input=input_bytes,
            capture_output=True, env=self.environment, timeout=60,
        )
        if result.returncode not in allowed:
            self.fail(
                f"git {' '.join(arguments)} failed rc={result.returncode}: "
                + result.stderr.decode("utf-8", "replace")
            )
        return result

    def blob(self, content=b"content\n"):
        return self.git("hash-object", "-w", "--stdin", input_bytes=content).stdout.strip()

    def mktree(self, entries):
        rows = []
        for mode, object_type, oid, raw_path in entries:
            rows.append(
                mode.encode("ascii") + b" " + object_type.encode("ascii")
                + b" " + oid + b"\t" + raw_path + b"\0"
            )
        return self.git("mktree", "-z", input_bytes=b"".join(rows)).stdout.decode("ascii").strip()

    def commit(self, tree, parent, message, *, second_parent=None):
        arguments = ["commit-tree", tree]
        if parent is not None:
            arguments.extend(["-p", parent])
        if second_parent is not None:
            arguments.extend(["-p", second_parent])
        return self.git(*arguments, input_bytes=message).stdout.decode("ascii").strip()

    def raw_commit(self, tree, parent, message, *, extra_headers=()):
        headers = [f"tree {tree}"]
        if parent is not None:
            headers.append(f"parent {parent}")
        headers.extend([
            "author Fixture Writer <fixture@example.invalid> 1 +0000",
            "committer Fixture Writer <fixture@example.invalid> 1 +0000",
            *extra_headers,
        ])
        raw = "\n".join(headers).encode("ascii") + b"\n\n" + message
        return self.git(
            "hash-object", "-t", "commit", "-w", "--stdin", input_bytes=raw,
        ).stdout.decode("ascii").strip()

    def tree_with(self, *items):
        entries = []
        for path, content, mode in items:
            object_type = "commit" if mode == "160000" else "blob"
            oid = (content.encode("ascii") if object_type == "commit"
                   else self.blob(content))
            entries.append((mode, object_type, oid, path.encode("utf-8")))
        return self.mktree(entries)


class IN01RepositoryIdentityTests(_RepositoryCase):
    def test_in_01_repository_indirection_is_frozen_and_alternates_refuse(self):
        linked = self.scratch / "linked"
        self.git("worktree", "add", "--quiet", "--detach", str(linked), self.base)
        inspected = provenance._Git(linked)
        self.assertEqual(linked.resolve(), inspected.root)
        self.assertTrue(inspected.git_dir.is_dir())
        self.assertNotEqual((linked / ".git").resolve(), inspected.git_dir)

        alternate_root = self.scratch / "shared-clone"
        cloned = subprocess.run(
            ["git", "clone", "--quiet", "--shared", str(self.root),
             str(alternate_root)], capture_output=True, env=self.environment, timeout=60,
        )
        if cloned.returncode:
            self.fail(cloned.stderr.decode("utf-8", "replace"))
        with self.assertRaisesRegex(ValidationError, "GIT_ALTERNATES_UNSAFE"):
            provenance.check_commit_range(alternate_root, self.base, self.base)

    def test_in_01_linked_worktree_commondir_control_is_frozen(self):
        linked = self.scratch / "linked-commondir"
        self.git("worktree", "add", "--quiet", "--detach", str(linked), self.base)
        inspected = provenance._Git(linked)
        commondir = inspected.git_dir / "commondir"
        self.assertTrue(commondir.is_file())

        # Keep the target unchanged while changing the control bytes.  Identity
        # verification must freeze the indirection itself, not only its target.
        commondir.write_bytes(b"../../.\n")
        with self.assertRaisesRegex(ValidationError, "REPOSITORY_IDENTITY_CHANGED"):
            inspected.text("rev-parse", "--git-common-dir")

    def test_in_01_repository_metadata_cap_at_n_and_n_plus_one(self):
        metadata = self.scratch / "repository-metadata"
        with patch.object(provenance, "_REPOSITORY_FILE_LIMIT", 4):
            metadata.write_bytes(b"1234")
            self.assertEqual(b"1234", provenance._Git._file_bytes(metadata))
            metadata.write_bytes(b"12345")
            with self.assertRaisesRegex(ValidationError, "REPOSITORY_METADATA_SIZE"):
                provenance._Git._file_bytes(metadata)


class IN02RangeEndpointTests(_RepositoryCase):
    def test_in_02_one_ref_grammar_and_commit_cap_at_n_and_n_plus_one(self):
        tree = self.tree_with(("one.txt", b"one\n", "100644"))
        first = self.commit(tree, self.base, self.message("one"))
        second = self.commit(tree, first, self.message("two"))
        self.git("update-ref", "refs/heads/topic", second)
        with patch.object(provenance, "_COMMIT_LIMIT", 1):
            self.assertEqual(
                "PASS",
                provenance.check_commit_range(
                    self.root, self.base, first,
                )["status"],
            )
            with self.assertRaisesRegex(ValidationError, "RANGE_COUNT"):
                provenance.check_commit_range(
                    self.root, "refs/heads/main", "refs/heads/topic",
                )


class IN03DefaultRefTests(_RepositoryCase):
    def test_in_03_origin_main_fallback_is_exact_and_reviewed_ref_is_validated(self):
        self.git("update-ref", "refs/heads/origin/main", self.base)
        observed = provenance.check_commit_range(
            self.root, self.base, self.base, origin_main="origin/main",
        )
        self.assertEqual(self.base, observed["origin_main"])
        policy = {
            "require_run_logs": False,
            "default_ref": "main^{tree}",
            "generated_paths": [],
            "generators": {},
        }
        provider = {
            "version": 1, "base_ref": "main^{tree}", "base": self.base,
            "pr": 95, "labels": [], "trusted_owner_ids": [],
        }
        with self.assertRaisesRegex(ValidationError, "REF_INVALID"):
            provenance.check_commit_range(
                self.root, self.base, self.base, reviewed_policy=policy,
                provider_observation=provider,
            )


class IN04RawCommitObjectTests(_RepositoryCase):
    def test_in_04_commit_encoding_and_message_cap_at_n_and_n_plus_one(self):
        exact = self.message("x")
        at_cap = self.raw_commit(self.empty_tree, self.base, exact)
        over_cap = self.raw_commit(self.empty_tree, self.base, self.message("xx"))
        with patch.object(provenance, "_MESSAGE_LIMIT", len(exact)):
            self.assertEqual(
                "PASS", provenance.check_commit_range(
                    self.root, self.base, at_cap,
                )["status"],
            )
            refused = provenance.check_commit_range(self.root, self.base, over_cap)
        self.assertRegex(refused["refusals"][0]["reason"], "MESSAGE_SIZE")

        encoded = self.raw_commit(
            self.empty_tree, self.base, exact, extra_headers=("encoding ISO-8859-1",),
        )
        refused = provenance.check_commit_range(self.root, self.base, encoded)
        self.assertRegex(refused["refusals"][0]["reason"], "COMMIT_ENCODING")

    def test_in_04_commit_object_and_header_caps_at_n_and_n_plus_one(self):
        def details(raw):
            digest = provenance.hashlib.sha1(
                f"commit {len(raw)}\0".encode("ascii") + raw,
            ).hexdigest()

            class CommitGit:
                object_format = "sha1"
                oid_length = 40

                @staticmethod
                def run(command, *arguments, **_kwargs):
                    output = raw if command == "cat-file" else b""
                    return subprocess.CompletedProcess(
                        [command, *arguments], 0, output, b"",
                    )

            return provenance._commit_details(CommitGit(), digest)

        headers = [
            f"tree {self.empty_tree}",
            f"parent {self.base}",
            "author Fixture Writer <fixture@example.invalid> 1 +0000",
            "committer Fixture Writer <fixture@example.invalid> 1 +0000",
            "gpgsig fixture",
        ]
        at_header_cap = "\n".join(headers).encode("ascii") + b"\n\n" + self.message()
        over_header_cap = (
            "\n".join([*headers, " continuation"]).encode("ascii")
            + b"\n\n" + self.message()
        )

        with patch.object(provenance, "_GIT_OUTPUT_LIMIT", len(at_header_cap)):
            self.assertEqual(self.base, details(at_header_cap)["parents"][0])
        with patch.object(provenance, "_GIT_OUTPUT_LIMIT", len(at_header_cap) - 1):
            with self.assertRaisesRegex(ValidationError, "COMMIT_OBJECT_SIZE"):
                details(at_header_cap)

        with patch.object(provenance, "_MESSAGE_LINE_LIMIT", len(headers)):
            self.assertEqual(self.base, details(at_header_cap)["parents"][0])
            with self.assertRaisesRegex(ValidationError, "COMMIT_HEADER_LINES"):
                details(over_header_cap)


class IN12UndecodablePathTests(_RepositoryCase):
    def test_in_12_undecodable_path_is_a_sanitized_per_commit_refusal(self):
        tree = self.mktree([("100644", "blob", self.blob(), b"bad\xff")])
        hostile = self.commit(tree, self.base, self.message("hostile"))
        safe = self.commit(tree, hostile, self.message("later"))
        result = provenance.check_commit_range(self.root, self.base, safe)
        self.assertEqual(hostile, result["refusals"][0]["sha"])
        self.assertRegex(result["refusals"][0]["reason"], r"PATH_UTF8.*sha256=")
        self.assertNotIn("bad", result["refusals"][0]["reason"])
        self.assertEqual("PASS", result["commits"][1]["status"])


class IN13PathSyntaxTests(_RepositoryCase):
    def test_in_13_portable_paths_and_record_cap_at_n_and_n_plus_one(self):
        for raw_path in (b"CON.txt", b"trail. ", "note\u0301.txt".encode()):
            with self.subTest(raw_path=raw_path):
                tree = self.mktree([("100644", "blob", self.blob(), raw_path)])
                head = self.commit(tree, self.base, self.message("unsafe"))
                refused = provenance.check_commit_range(self.root, self.base, head)
                self.assertRegex(refused["refusals"][0]["reason"], "PATH_INVALID")

        one = self.mktree([("100644", "blob", self.blob(b"1"), b"one")])
        two = self.mktree([
            ("100644", "blob", self.blob(b"1"), b"one"),
            ("100644", "blob", self.blob(b"2"), b"two"),
        ])
        with patch.object(provenance, "_PATH_RECORD_LIMIT", 1):
            self.assertEqual("PASS", provenance.check_commit_range(
                self.root, self.base, self.commit(one, self.base, self.message("one")),
            )["status"])
            refused = provenance.check_commit_range(
                self.root, self.base, self.commit(two, self.base, self.message("two")),
            )
        self.assertRegex(refused["refusals"][0]["reason"], "RAW_DIFF_COUNT")


class IN14RenameTests(_RepositoryCase):
    def test_in_14_rename_is_explicit_delete_plus_add(self):
        blob = self.blob(b"same\n")
        old_tree = self.mktree([("100644", "blob", blob, b"old.txt")])
        new_tree = self.mktree([("100644", "blob", blob, b"new.txt")])
        old = self.commit(old_tree, self.base, self.message("old"))
        new = self.commit(new_tree, old, self.message("rename"))
        result = provenance.check_commit_range(self.root, old, new)
        self.assertEqual(
            [("new.txt", "A"), ("old.txt", "D")],
            sorted((entry["path"], entry["action"])
                   for entry in result["commits"][0]["changed_entries"]),
        )


class IN15EntryTypeTests(_RepositoryCase):
    def test_in_15_generated_symlink_gitlink_and_type_change_refuse_by_name(self):
        script = self.message("generated", writer="script/range-generator")
        symlink_tree = self.mktree([
            ("120000", "blob", self.blob(b"target"), b"generated.txt"),
        ])
        symlink = self.commit(symlink_tree, self.base, script)
        refused = provenance.check_commit_range(
            self.root, self.base, symlink, generated_paths=["generated.txt"],
            reproduce_generated=lambda *_: {"generated.txt": b"target"},
        )
        self.assertRegex(refused["refusals"][0]["reason"], "GENERATED_ENTRY_INVALID")

        blob_tree = self.mktree([
            ("100644", "blob", self.blob(b"target"), b"generated.txt"),
        ])
        changed = self.commit(blob_tree, symlink, script)
        refused = provenance.check_commit_range(
            self.root, symlink, changed, generated_paths=["generated.txt"],
            reproduce_generated=lambda *_: {"generated.txt": b"target"},
        )
        self.assertRegex(refused["refusals"][0]["reason"], "GENERATED_ENTRY_INVALID")

        gitlink_tree = self.mktree([
            ("160000", "commit", self.base.encode("ascii"), b"generated.txt"),
        ])
        gitlink = self.commit(gitlink_tree, self.base, script)
        refused = provenance.check_commit_range(
            self.root, self.base, gitlink, generated_paths=["generated.txt"],
            reproduce_generated=lambda *_: {"generated.txt": b"target"},
        )
        self.assertRegex(refused["refusals"][0]["reason"], "GENERATED_ENTRY_INVALID")


class IN16GeneratedPathTests(_RepositoryCase):
    def test_in_16_generated_paths_are_validated_and_deduplicated_portably(self):
        head = self.commit(self.empty_tree, self.base, self.message("empty"))
        with self.assertRaisesRegex(ValidationError, "GENERATED_PATH_DUPLICATE"):
            provenance.check_commit_range(
                self.root, self.base, head,
                generated_paths=["Generated.txt", "generated.txt"],
            )
        with patch.object(provenance, "_PATH_RECORD_LIMIT", 1):
            provenance.check_commit_range(
                self.root, self.base, head, generated_paths=["one.txt"],
            )
            with self.assertRaisesRegex(ValidationError, "GENERATED_PATH_COUNT"):
                provenance.check_commit_range(
                    self.root, self.base, head,
                    generated_paths=["one.txt", "two.txt"],
                )


class IN17GeneratorBytesTests(_RepositoryCase):
    def test_in_17_reproduced_bytes_and_blob_cap_at_n_and_n_plus_one(self):
        tree = self.mktree([("100644", "blob", self.blob(b"12345"), b"generated.txt")])
        head = self.commit(
            tree, self.base, self.message("generated", writer="script/range-generator"),
        )
        with patch.object(provenance, "_GENERATED_BLOB_LIMIT", 5):
            passed = provenance.check_commit_range(
                self.root, self.base, head, generated_paths=["generated.txt"],
                reproduce_generated=lambda *_: {"generated.txt": b"12345"},
            )
            self.assertEqual("PASS", passed["status"])
        with patch.object(provenance, "_GENERATED_BLOB_LIMIT", 4):
            refused = provenance.check_commit_range(
                self.root, self.base, head, generated_paths=["generated.txt"],
                reproduce_generated=lambda *_: {"generated.txt": b"12345"},
            )
        self.assertRegex(refused["refusals"][0]["reason"], "GENERATOR_BLOB_SIZE")

        total_tree = self.mktree([
            ("100644", "blob", self.blob(b"12345"), b"generated-a.txt"),
            ("100644", "blob", self.blob(b"67890"), b"generated-b.txt"),
        ])
        total_head = self.commit(
            total_tree, self.base,
            self.message("generated total", writer="script/range-generator"),
        )
        paths = ["generated-a.txt", "generated-b.txt"]
        reproduced = {"generated-a.txt": b"12345", "generated-b.txt": b"67890"}
        with patch.object(provenance, "_GENERATED_TOTAL_LIMIT", 10):
            self.assertEqual("PASS", provenance.check_commit_range(
                self.root, self.base, total_head, generated_paths=paths,
                reproduce_generated=lambda *_: reproduced,
            )["status"])
        with patch.object(provenance, "_GENERATED_TOTAL_LIMIT", 9):
            refused = provenance.check_commit_range(
                self.root, self.base, total_head, generated_paths=paths,
                reproduce_generated=lambda *_: reproduced,
            )
        self.assertRegex(refused["refusals"][0]["reason"], "GENERATOR_TOTAL_SIZE")


class IN18MergeTreeTests(_RepositoryCase):
    def test_in_18_merge_tree_output_is_bounded_framed_and_format_specific(self):
        oid = b"a" * 40
        self.assertEqual(("a" * 40, []), provenance._decode_merge_tree_output(
            oid + b"\0", 40,
        ))
        for output in (oid + b"\0bad\xff\0", b"a" * 64 + b"\0", oid):
            with self.subTest(output=output), self.assertRaisesRegex(
                    ValidationError, "MERGE_TREE_"):
                provenance._decode_merge_tree_output(output, 40)

    def test_in_18_merge_tree_output_cap_at_n_and_n_plus_one(self):
        output = b"a" * 40 + b"\0"
        with patch.object(provenance, "_GIT_OUTPUT_LIMIT", len(output)):
            self.assertEqual(
                ("a" * 40, []),
                provenance._decode_merge_tree_output(output, 40),
            )
        with patch.object(provenance, "_GIT_OUTPUT_LIMIT", len(output) - 1):
            with self.assertRaisesRegex(ValidationError, "MERGE_TREE_SIZE"):
                provenance._decode_merge_tree_output(output, 40)


class IN43RawHeaderTests(_RepositoryCase):
    def test_in_43_raw_commit_headers_refuse_unknown_and_wrong_format_oids(self):
        unknown = self.raw_commit(
            self.empty_tree, self.base, self.message(), extra_headers=("unknown value",),
        )
        refused = provenance.check_commit_range(self.root, self.base, unknown)
        self.assertRegex(refused["refusals"][0]["reason"], "COMMIT_HEADER_UNKNOWN")

        raw = (
            b"tree " + b"a" * 64 + b"\n"
            + f"parent {self.base}\n".encode("ascii")
            + b"author Fixture Writer <fixture@example.invalid> 1 +0000\n"
            + b"committer Fixture Writer <fixture@example.invalid> 1 +0000\n\n"
            + self.message()
        )
        wrong = self.git(
            "hash-object", "-t", "commit", "-w", "--stdin", "--literally",
            input_bytes=raw,
        ).stdout.decode("ascii").strip()
        with self.assertRaisesRegex(ValidationError, "COMMIT_HEADER_OID"):
            provenance._commit_details(provenance._Git(self.root), wrong)


class IN44RepositoryConfigTests(_RepositoryCase):
    def test_in_44_unsafe_local_config_refuses_and_shallow_boundary_is_named(self):
        self.git("config", "merge.fixture.driver", "false")
        with self.assertRaisesRegex(ValidationError, "GIT_CONFIG_UNSAFE"):
            provenance.check_commit_range(self.root, self.base, self.base)
        self.git("config", "--unset", "merge.fixture.driver")

        first = self.commit(
            self.tree_with(("one", b"one", "100644")), self.base,
            self.message("first"),
        )
        second = self.commit(
            self.tree_with(("two", b"two", "100644")), first,
            self.message("second"),
        )
        head = self.commit(
            self.tree_with(("three", b"three", "100644")), second,
            self.message("head"),
        )
        self.git("update-ref", "refs/heads/main", head)
        shallow_root = self.scratch / "shallow"
        clone = subprocess.run(
            ["git", "clone", "--quiet", "--depth=2", self.root.resolve().as_uri(),
             str(shallow_root)], capture_output=True, env=self.environment, timeout=60,
        )
        if clone.returncode:
            self.fail(clone.stderr.decode("utf-8", "replace"))
        fetched = subprocess.run(
            ["git", "-C", str(shallow_root), "fetch", "--quiet", "--depth=1",
             "origin", self.base], capture_output=True, env=self.environment, timeout=60,
        )
        if fetched.returncode:
            self.fail(fetched.stderr.decode("utf-8", "replace"))
        with self.assertRaisesRegex(ValidationError, "SHALLOW_RANGE_BOUNDARY"):
            provenance.check_commit_range(shallow_root, self.base, head)
        self.assertEqual(
            "PASS", provenance.check_commit_range(
                shallow_root, second, head,
            )["status"],
        )

    def test_in_44_submodule_diff_configuration_is_closed(self):
        for key in ("diff.ignoreSubmodules", "submodule.foreign.ignore"):
            with self.subTest(key=key):
                self.git("config", "--local", key, "all")
                try:
                    with self.assertRaisesRegex(ValidationError, "GIT_CONFIG_UNSAFE"):
                        provenance.check_commit_range(self.root, self.base, self.base)
                finally:
                    self.git("config", "--local", "--unset-all", key)

    def test_in_44_merge_tree_configuration_is_closed(self):
        controls = (
            ("merge.conflictStyle", "diff3"),
            ("merge.default", "binary"),
            ("merge.directoryRenames", "true"),
            ("merge.fixture.driver", "false"),
            ("merge.fixture.recursive", "binary"),
            ("merge.renameLimit", "1"),
            ("merge.renames", "false"),
            ("merge.renormalize", "true"),
            ("merge.verbosity", "5"),
            ("diff.renameLimit", "1"),
            ("diff.renames", "false"),
        )
        for key, value in controls:
            with self.subTest(key=key):
                self.git("config", "--local", key, value)
                try:
                    with self.assertRaisesRegex(ValidationError, "GIT_CONFIG_UNSAFE"):
                        provenance.check_commit_range(self.root, self.base, self.base)
                finally:
                    self.git("config", "--local", "--unset-all", key)


class IN46GeneratorIdentityTests(_RepositoryCase):
    def test_in_46_generator_mapping_is_closed_and_never_crashes(self):
        tree = self.tree_with(("generated.txt", b"ok", "100644"))
        head = self.commit(
            tree, self.base, self.message("generated", writer="script/range-generator"),
        )
        policy = {
            "require_run_logs": False, "default_ref": "refs/heads/main",
            "generated_paths": ["generated.txt"], "generators": [],
        }
        provider = {
            "version": 1, "base_ref": "main", "base": self.base,
            "pr": 95, "labels": [], "trusted_owner_ids": [],
        }
        with self.assertRaisesRegex(ValidationError, "GENERATOR_REGISTRY_INVALID"):
            provenance.check_commit_range(
                self.root, self.base, head, reviewed_policy=policy,
                provider_observation=provider, runtime_registry={"generators": {}},
            )


class B11RawNoRenameTests(_RepositoryCase):
    def test_b11_raw_z_records_reject_collapsed_rename_status(self):
        oid = "a" * 40
        raw = f":100644 100644 {oid} {oid} R100\0old\0new\0".encode("ascii")
        with self.assertRaisesRegex(ValidationError, "RENAME_COLLAPSED"):
            provenance._parse_raw_diff(raw, 40)

        added = (
            f":000000 100644 {'0' * 40} {oid} A\0path\0"
        ).encode("ascii")
        with patch.object(provenance, "_GIT_OUTPUT_LIMIT", len(added)):
            self.assertEqual("path", provenance._parse_raw_diff(added, 40)[0]["path"])
        with patch.object(provenance, "_GIT_OUTPUT_LIMIT", len(added) - 1):
            with self.assertRaisesRegex(ValidationError, "RAW_DIFF_SIZE"):
                provenance._parse_raw_diff(added, 40)


class B13PortablePathContractTests(_RepositoryCase):
    def test_b13_windows_and_linux_share_casefold_reserved_and_separator_rules(self):
        for value in ("aux", "LPT9.log", "COM¹.txt", "CONIN$", "name.",
                      "name ", "a\\b", "a:b", "a?b", 'a"b'):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                provenance._validate_object_path(value)
        oid = "a" * 40
        raw = (
            f":000000 100644 {'0' * 40} {oid} A\0Name.txt\0"
            f":000000 100644 {'0' * 40} {oid} A\0name.txt\0"
        ).encode("ascii")
        with self.assertRaisesRegex(ValidationError, "PATH_DUPLICATE"):
            provenance._parse_raw_diff(raw, 40)


class B20RefGrammarTests(_RepositoryCase):
    def test_b20_only_full_oids_heads_and_remotes_are_accepted(self):
        accepted = (self.base, "refs/heads/main", "refs/remotes/origin/main")
        for value in accepted:
            with self.subTest(value=value):
                self.assertEqual(value, provenance.validate_ref_locator(value))
        for value in ("main", "origin/main", "HEAD", "refs/tags/v1", "refs/heads/a..b"):
            with self.subTest(value=value), self.assertRaisesRegex(
                    ValidationError, "REF_INVALID"):
                provenance.validate_ref_locator(value)


class B21Sha256RepositoryTests(_RepositoryCase):
    object_format = "sha256"

    def test_b21_sha256_repository_runs_end_to_end(self):
        tree = self.tree_with(("sha256.txt", b"sha256\n", "100644"))
        head = self.commit(tree, self.base, self.message("sha256"))
        result = provenance.check_commit_range(self.root, self.base, head)
        self.assertEqual(("PASS", "sha256", 64), (
            result["status"], result["object_format"], len(result["head"]),
        ))
        with self.assertRaisesRegex(ValidationError, "REF_FORMAT"):
            provenance.check_commit_range(self.root, "a" * 40, head)


if __name__ == "__main__":
    unittest.main()
