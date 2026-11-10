import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
sys.path.insert(0, str(ROOT / ".agentic/tests"))
sys.path.insert(0, str(ROOT / "scripts"))

from agentic import ValidationError  # noqa: E402
from agentic.canonical import load_yaml, sha256  # noqa: E402
from agentic.installer import CONFIG, INSTALLED, PROVENANCE, install, verify_installed  # noqa: E402
from agentic.safeio import Tree  # noqa: E402
from agentic.upgrade import identify_installation, immutable_from_source_manifest, load_known_versions  # noqa: E402
from test_awf238_same_version_identity import complete_inventory  # noqa: E402
from upgrade_fixtures import fixture_blob, fixture_manifest  # noqa: E402
from source_only import COMMITTED_GIT_REASON, committed_git_tuple, skip_unless_source_repo  # noqa: E402


HERMES_COMMIT = "1f5f146352ff0a8222a6be1ef9186e4609b11033"
HERMES_MANIFEST_SHA256 = "c3bed1ce03703661de65b32f5a5071005e5de27f4ce0d36f746315609d564d25"
HERMES_MANAGED_SHA256 = "ee7e3d412140113fe69bf8feb1b022dc8c4c01cf0465efc970f297a2209cd5f6"


def git_blob(path):
    completed = subprocess.run(
        ["git", "show", f"{HERMES_COMMIT}:{path}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode(errors="replace"))
    return completed.stdout


def tree_digest(root):
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes())
        for path in root.rglob("*")
        if path.is_file()
    }


@skip_unless_source_repo(
    "historical commit-backed receipt tests require the AWF source repository",
    "scripts/build_release.py",
)
class UpgradeReceiptTests(unittest.TestCase):
    def setUp(self):
        if committed_git_tuple(ROOT) is None:
            self.skipTest(COMMITTED_GIT_REASON)
        self.destination = Path(tempfile.gettempdir()) / ("awf-upgrade-receipt-" + uuid.uuid4().hex)
        self.destination.mkdir()
        self.addCleanup(self.cleanup_destination)
        self.source_manifest_raw = git_blob("MANIFEST.json")
        self.source_manifest = json.loads(self.source_manifest_raw)
        self.immutable = immutable_from_source_manifest(self.source_manifest)

    def cleanup_destination(self):
        if not self.destination.exists():
            return
        for path in self.destination.rglob("*"):
            if path.is_file():
                path.chmod(stat.S_IWRITE | stat.S_IREAD)
        shutil.rmtree(self.destination)

    def write(self, relative, raw):
        path = self.destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def materialize_receipt(self):
        self.assertEqual(sha256(self.source_manifest_raw), HERMES_MANIFEST_SHA256)
        for relative, expected in self.immutable.items():
            raw = git_blob(relative)
            self.assertEqual(sha256(raw), expected, relative)
            self.write(relative, raw)

        config_raw = git_blob(CONFIG)
        self.write(CONFIG, config_raw)
        receipt = {
            "template_version": "1.9.1",
            "source_manifest_sha256": HERMES_MANIFEST_SHA256,
            "immutable_files": self.immutable,
            "source_manifest_json": self.source_manifest_raw.decode("utf-8"),
            "initial_config_sha256": sha256(config_raw),
            "mutable_paths": [CONFIG, ".github/CODEOWNERS"],
            "install_id": "11111111-1111-4111-8111-111111111111",
        }
        self.write(INSTALLED, json.dumps(receipt, indent=2).encode("utf-8") + b"\n")

        provenance = json.loads(git_blob(PROVENANCE))
        provenance["installation"].update(
            install_id=receipt["install_id"],
            source_manifest_sha256=HERMES_MANIFEST_SHA256,
        )
        self.write(PROVENANCE, json.dumps(provenance, indent=2).encode("utf-8") + b"\n")

    def assert_registered_identity(self):
        table = load_known_versions((ROOT / ".agentic/upgrade/known-versions.json").read_bytes())
        variants = table["versions"]["1.9.1"]["_receipt_variants"]
        hermes = next(item for item in variants
                      if item["source_manifest_sha256"] == HERMES_MANIFEST_SHA256)
        self.assertEqual(hermes["managed_file_count"], 209)
        self.assertEqual(hermes["managed_manifest_sha256"], HERMES_MANAGED_SHA256)
        self.assertEqual(hermes["provenance"], {
            "kind": "source commit",
            "repository": "this-repository",
            "commit": HERMES_COMMIT,
        })
        with Tree(self.destination) as tree:
            identity = identify_installation(tree, table, (self.destination / CONFIG).read_bytes(),
                                             (self.destination / INSTALLED).read_bytes())
        self.assertEqual('1.9.1', identity['version'])
        self.assertEqual('11111111-1111-4111-8111-111111111111', identity['install_id'])
        self.assertEqual(self.immutable, identity['immutable_files'])
        return identity

    def test_registered_commit_backed_191_unresolved_configuration_refuses_without_writes(self):
        self.materialize_receipt()
        self.assert_registered_identity()
        # The unresolved source template intentionally has no repository binding;
        # qualification inventory requires one. Preserve every entry and its
        # supported metadata without inventing owner repository choices.
        before, files = complete_inventory(self.destination), tree_digest(self.destination)
        for dry_run in (True, False):
            with self.subTest(dry_run=dry_run):
                with self.assertRaisesRegex(ValidationError,
                        r'Upgrade configuration rejected: .*PROJECT_CONFIG.yaml.*resolve these choices.*no files changed'):
                    install(ROOT, self.destination, sha256((ROOT / 'MANIFEST.json').read_bytes()),
                            mode='upgrade', configure=True, discover=False, dry_run=dry_run)
                self.assertEqual(before, complete_inventory(self.destination))
                self.assertEqual(files, tree_digest(self.destination))

    def test_registered_commit_backed_191_receipt_upgrades(self):
        self.materialize_receipt()
        identity = self.assert_registered_identity()
        receipt_raw = (self.destination / INSTALLED).read_bytes()
        provenance_raw = (self.destination / PROVENANCE).read_bytes()
        # Explicit synthetic owner choices from the verified 1.9.1 fixture;
        # the registered managed bytes and installation records remain intact.
        fixture = fixture_manifest('1.9.1')
        owner = load_yaml(fixture_blob(fixture['owner_files'][CONFIG]['sha256']))
        config = load_yaml((self.destination / CONFIG).read_bytes())
        config['project']['id'] = owner['project']['id']
        config['validation']['commands'] = owner['validation']['commands']
        self.write(CONFIG, b'# Synthetic disposable owner configuration.\n' +
                   (json.dumps(config, indent=2) + '\n').encode())
        self.assertEqual(receipt_raw, (self.destination / INSTALLED).read_bytes())
        self.assertEqual(provenance_raw, (self.destination / PROVENANCE).read_bytes())
        self.assert_registered_identity()
        target_manifest_sha256 = sha256((ROOT / "MANIFEST.json").read_bytes())
        result = install(
            ROOT,
            self.destination,
            target_manifest_sha256,
            mode="upgrade",
            configure=True,
            discover=False,
        )

        self.assertEqual(result["upgrade"]["detected_version"], "1.9.1")
        self.assertEqual(verify_installed(self.destination), target_manifest_sha256)
        after = load_yaml((self.destination / CONFIG).read_bytes())
        self.assertEqual(owner['project']['id'], after['project']['id'])
        self.assertEqual(owner['validation']['commands'], after['validation']['commands'])
        self.assertEqual(identity['install_id'], json.loads((self.destination / INSTALLED).read_bytes())['install_id'])
        self.assertEqual(identity['install_id'], load_yaml((self.destination / PROVENANCE).read_bytes())['installation']['install_id'])

    def test_unknown_191_receipt_fails_with_recovery_instructions_and_no_writes(self):
        self.materialize_receipt()
        receipt_path = self.destination / INSTALLED
        receipt = json.loads(receipt_path.read_bytes())
        receipt["source_manifest_sha256"] = "9" * 64
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        before = tree_digest(self.destination)

        with self.assertRaisesRegex(
            ValidationError,
            r"not registered.*Back up.*fresh install.*owner-approved.*no files changed",
        ):
            install(
                ROOT,
                self.destination,
                sha256((ROOT / "MANIFEST.json").read_bytes()),
                mode="upgrade",
                configure=True,
                discover=False,
            )

        self.assertEqual(tree_digest(self.destination), before)


if __name__ == "__main__":
    unittest.main()
