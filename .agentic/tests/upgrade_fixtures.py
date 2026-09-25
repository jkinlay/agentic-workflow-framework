"""Offline materialisation of byte-exact historical AWF installations."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = ROOT / ".agentic/tests/fixtures/upgrades"
BLOB_ROOT = FIXTURE_ROOT / "blobs"
INDEX = FIXTURE_ROOT / "versions.json"
CONFIG = ".agentic/PROJECT_CONFIG.yaml"
INSTALLED = ".agentic/installed-manifest.json"
PROVENANCE = ".agentic/workflow-version.yaml"
OPERATING = "OPERATING_CONFIG.yaml"
NEXT = {"1.8.3": "1.8.9", "1.8.9": "1.9.1", "1.9.1": "1.9.2", "1.9.2": "1.9.3"}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(path):
    return json.loads(path.read_bytes())


def fixture_index():
    value = _json(INDEX)
    if value.get("format") != "awf-upgrade-fixture-index-1":
        raise AssertionError("Unsupported upgrade fixture index")
    return value


def fixture_manifest(version):
    index = fixture_index()
    try:
        entry = index["versions"][version]
    except KeyError as exc:
        raise AssertionError("Unknown upgrade fixture version: " + version) from exc
    path = FIXTURE_ROOT / entry["manifest"]
    raw = path.read_bytes()
    if sha256(raw) != entry["manifest_sha256"]:
        raise AssertionError("Upgrade fixture manifest digest mismatch: " + version)
    value = json.loads(raw)
    if value.get("format") != "awf-upgrade-fixture-1" or value.get("version") != version:
        raise AssertionError("Upgrade fixture manifest identity mismatch: " + version)
    return value


def fixture_blob(expected):
    raw = (BLOB_ROOT / expected).read_bytes()
    if sha256(raw) != expected:
        raise AssertionError("Upgrade fixture blob digest mismatch: " + expected)
    return raw


def _outside_repository(destination):
    root = ROOT.resolve()
    target = Path(destination).resolve()
    if target == root or root in target.parents:
        raise AssertionError("Upgrade fixtures must be materialised outside the repository")
    return target


def _write(destination, relative, raw, mode):
    path = destination / Path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    if os.name != "nt":
        path.chmod(0o755 if mode == "100755" else 0o644)


def materialize(version, destination):
    """Write a historical fixture plus synthetic owner files to an empty temp dir."""
    destination = _outside_repository(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if any(destination.iterdir()):
        raise AssertionError("Upgrade fixture destination must be empty")
    manifest = fixture_manifest(version)
    for relative, entry in sorted(manifest["managed_files"].items()):
        _write(destination, relative, fixture_blob(entry["sha256"]), entry["mode"])
    for relative, entry in sorted(manifest["owner_files"].items()):
        _write(destination, relative, fixture_blob(entry["sha256"]), entry["mode"])
    state = {
        ".agentic-state/routing/history.json": b'{"fixture":"routing-history"}\n',
        ".agentic-state/reviews/history.json": b'{"fixture":"review-history"}\n',
        ".agentic-state/lifecycle/history.json": b'{"fixture":"lifecycle-history"}\n',
        ".agentic-state/legacy-note.txt": b"synthetic legacy state\r\n",
    }
    for relative, raw in state.items():
        _write(destination, relative, raw, "100644")
    return {
        "version": version,
        "manifest": manifest,
        "configuration": (destination / CONFIG).read_bytes(),
        "codeowners": (destination / ".github/CODEOWNERS").read_bytes(),
        "instructions": (destination / "PROJECT_INSTRUCTIONS.md").read_bytes(),
        "operating": (destination / OPERATING).read_bytes() if (destination / OPERATING).is_file() else None,
        "state": state,
    }


def verify_materialized(version, destination):
    """Verify fixture membership and every receipt-managed byte without Git."""
    destination = _outside_repository(destination)
    manifest = fixture_manifest(version)
    receipt = json.loads((destination / INSTALLED).read_bytes())
    if receipt.get("template_version") != version:
        raise AssertionError("Materialized receipt version mismatch")
    if receipt.get("source_manifest_sha256") != manifest["source_manifest_sha256"]:
        raise AssertionError("Materialized source-manifest digest mismatch")
    expected = {path: entry["sha256"] for path, entry in manifest["managed_files"].items()}
    if receipt.get("immutable_files") != expected:
        raise AssertionError("Materialized receipt membership mismatch")
    for relative, expected_hash in expected.items():
        path = destination / Path(relative)
        actual = sha256(path.read_bytes()) if path.is_file() else None
        if actual != expected_hash:
            raise AssertionError(f"Materialized fixture mismatch: {relative} expected={expected_hash} actual={actual}")
    return expected


def advance_one_fixture_step(destination, version):
    """Apply one registered pure migration and install the next real fixture tree."""
    current = fixture_manifest(version)
    next_version = NEXT[version]
    if next_version == "1.9.3":
        raise AssertionError("The final step must use the real 1.9.3 installer")
    following = fixture_manifest(next_version)
    verify_materialized(version, destination)

    from agentic.upgrade import MigrationBundle, migrate_step
    from types import MappingProxyType

    root = _outside_repository(destination)
    config = (root / CONFIG).read_bytes()
    receipt = (root / INSTALLED).read_bytes()
    operating = (root / OPERATING).read_bytes() if (root / OPERATING).is_file() else None
    state = {path.relative_to(root).as_posix(): path.read_bytes()
             for path in root.joinpath(".agentic-state").rglob("*") if path.is_file()}
    migrated = migrate_step(MigrationBundle(config, operating, receipt, MappingProxyType(state)), version, next_version)

    old_paths = set(current["managed_files"])
    new_paths = set(following["managed_files"])
    for relative in sorted(old_paths - new_paths):
        (root / Path(relative)).unlink()
    for relative, entry in sorted(following["managed_files"].items()):
        _write(root, relative, fixture_blob(entry["sha256"]), entry["mode"])
    (root / CONFIG).write_bytes(migrated.project_config)
    for relative in (INSTALLED, PROVENANCE):
        entry = following["owner_files"][relative]
        _write(root, relative, fixture_blob(entry["sha256"]), entry["mode"])
    verify_materialized(next_version, root)
    return next_version


def file_tree(root):
    root = Path(root)
    return {path.relative_to(root).as_posix(): sha256(path.read_bytes())
            for path in sorted(root.rglob("*")) if path.is_file()}


def managed_tree_from_receipt(root):
    root = Path(root)
    receipt = json.loads((root / INSTALLED).read_bytes())
    return {relative: sha256((root / Path(relative)).read_bytes())
            for relative in sorted(receipt["immutable_files"])}
