"""Manifest-verified, journaled installation of managed workflow files."""
from __future__ import annotations
import base64
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import uuid

from . import ValidationError, VERSION
from .canonical import load_yaml, loads, now_text, sha256
from .safeio import Tree, relative_parts
from .providers.github import load_observation_report, validate_codeowner

MANIFEST = "MANIFEST.json"
LOCK = ".agentic-install/lock"
JOURNAL = ".agentic-install/journal.json"
MARKER = ".agentic/INSTALLING.json"
INSTALLED = ".agentic/installed-manifest.json"
CONFIG = ".agentic/PROJECT_CONFIG.yaml"
PROVENANCE = ".agentic/workflow-version.yaml"
CODEOWNERS = ".github/CODEOWNERS"
GITIGNORE = ".gitignore"
GITIGNORE_TEMPLATE = ".agentic/templates/operating.gitignore"


def json_bytes(value):
    return json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"


def managed(path):
    return path in {"AGENTS.md", ".github/PULL_REQUEST_TEMPLATE.md", CODEOWNERS} or path.startswith(".agentic/")


def merge_operating_ignores(existing, required):
    """Append the narrow release block without rewriting project-owned bytes."""
    if not required.endswith(b"\n") or b"\x00" in required:
        raise ValidationError("Invalid operating ignore template")
    if existing is None:
        return required
    # Keep this block last: earlier project negations cannot unignore locks.
    # A later owner edit is preserved, with a fresh block appended on adoption.
    if existing.endswith(required):
        return existing
    return existing + (b"\n" if existing and not existing.endswith(b"\n") else b"") + required


def verify_release(tree, expected_digest=None):
    raw = tree.read(MANIFEST)
    if expected_digest is not None and sha256(raw) != expected_digest:
        raise ValidationError("Release manifest does not match the approved digest")
    manifest = loads(raw.decode("utf-8"))
    if set(manifest) != {"format", "template_version", "files"} or manifest["format"] != "awf-manifest-1" or manifest["template_version"] != VERSION:
        raise ValidationError("Unsupported release manifest")
    actual = set(tree.file_list(exclude_root_git=True)) - {MANIFEST, "MANIFEST.md"}
    if actual != set(manifest["files"]):
        raise ValidationError("Release manifest file membership mismatch")
    folded = [path.casefold() for path in actual]
    if len(set(folded)) != len(folded):
        raise ValidationError("Case-colliding paths in release")
    content = {}
    for path, expected in manifest["files"].items():
        relative_parts(path)
        data = tree.read(path)
        if expected != sha256(data):
            raise ValidationError(f"Source digest mismatch: {path}")
        content[path] = data
    return sha256(raw), content


@contextmanager
def install_lock(tree, lock_path=LOCK):
    parent, handle, name = tree.parent(lock_path, create=True)
    tree.inspect(lock_path)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    if os.name == "nt":
        import msvcrt
        from .safeio import win_open
        fd = msvcrt.open_osfhandle(win_open(parent / name, lock=True), os.O_RDWR | os.O_BINARY)
    else:
        fd = os.open(name, flags, 0o600, dir_fd=handle)
    locked = False
    try:
        if os.fstat(fd).st_nlink != 1:
            raise ValidationError("Installer lock has multiple links")
        if os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        locked = True
        yield
    except (BlockingIOError, PermissionError) as exc:
        raise ValidationError("Another installer owns the destination lock") from exc
    finally:
        if locked:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _exists_read(tree, path):
    return tree.read(path) if tree.inspect(path) is not None else None


def assert_quiescent(config_bytes):
    if config_bytes is None:
        return
    import yaml
    from .canonical import validate_value
    try:
        value = load_yaml(config_bytes)
        validate_value(value)
        if not isinstance(value, dict):
            raise ValidationError("Existing configuration is not an object")
        controller = value.get("controller", {})
        if not isinstance(controller, dict):
            raise ValidationError("$.controller must be an object with every automation switch false; correct the existing configuration before installing")
        if any(controller.get(key) is not False for key in ["dispatch_enabled", "auto_dispatch", "auto_request_critic", "auto_resume_amendments", "auto_transition_jira"]):
            raise ValidationError("Pause all automation switches before installing/upgrading")
    except yaml.YAMLError as exc:
        raise ValidationError("Cannot verify existing configuration quiescence") from exc


def ensure_usable(root):
    with Tree(root) as tree:
        if tree.inspect(MARKER) is not None or tree.inspect(JOURNAL) is not None:
            raise ValidationError("Installation is incomplete; use bootstrap --recover before running tools")


def verify_installed(root):
    ensure_usable(root)
    with Tree(root) as tree:
        if tree.inspect(INSTALLED) is None:
            return verify_release(tree)[0]
        manifest = loads(tree.read(INSTALLED).decode())
        if manifest.get("template_version") != VERSION:
            raise ValidationError("Installed template version mismatch")
        if "source_manifest_json" not in manifest:
            raise ValidationError("Current-version receipt requires source_manifest_json; use an explicit reviewed migration for legacy receipts")
        source_raw = manifest["source_manifest_json"]
        if not isinstance(source_raw, str) or sha256(source_raw.encode("utf-8")) != manifest["source_manifest_sha256"]:
            raise ValidationError("Installed embedded source manifest digest mismatch")
        source_manifest = loads(source_raw)
        if source_manifest.get("format") != "awf-manifest-1" or source_manifest.get("template_version") != VERSION:
            raise ValidationError("Installed embedded source manifest is invalid")
        expected = {path: digest for path, digest in source_manifest["files"].items()
                    if managed(path) and path not in {CONFIG, PROVENANCE, CODEOWNERS}}
        if manifest["immutable_files"] != expected:
            raise ValidationError("Installed immutable membership differs from its source manifest")
        for path, digest in manifest["immutable_files"].items():
            if sha256(tree.read(path)) != digest:
                raise ValidationError(f"Installed managed file changed: {path}")
        version = loads(tree.read(PROVENANCE).decode())
        if version["template"]["version"] != VERSION or version["installation"]["source_manifest_sha256"] != manifest["source_manifest_sha256"]:
            raise ValidationError("Installed provenance is inconsistent")
        return manifest["source_manifest_sha256"]


def rollback(tree, journal):
    if not isinstance(journal, dict) or set(journal) != {"format", "transaction_id", "files"} or journal["format"] != "awf-install-journal-1":
        raise ValidationError("Invalid recovery journal")
    uuid.UUID(journal["transaction_id"])
    if not isinstance(journal["files"], list) or len(journal["files"]) > 10000:
        raise ValidationError("Invalid recovery file inventory")
    seen = set()
    for item in journal["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "old", "new_sha256"}:
            raise ValidationError("Invalid recovery file entry")
        relative_parts(item["path"])
        if (not managed(item["path"]) and item["path"] != GITIGNORE) or item["path"] == MARKER or item["path"].casefold() in seen or not re.fullmatch(r"[0-9a-f]{64}", item["new_sha256"]):
            raise ValidationError("Unsafe or duplicate recovery path/digest")
        seen.add(item["path"].casefold())
        if item["old"] is not None:
            base64.b64decode(item["old"], validate=True)
    # Do not clobber edits that were neither the old nor proposed installer bytes.
    for item in journal["files"]:
        current = _exists_read(tree, item["path"])
        old = base64.b64decode(item["old"], validate=True) if item["old"] is not None else None
        if current != old and (current is None or sha256(current) != item["new_sha256"]):
            raise ValidationError(f"Recovery found an external edit at {item['path']}; marker retained")
    for item in reversed(journal["files"]):
        if item["old"] is None:
            tree.unlink(item["path"])
        else:
            tree.write(item["path"], base64.b64decode(item["old"], validate=True))
    tree.unlink(MARKER)
    tree.unlink(JOURNAL)


def recover(destination):
    with Tree(destination) as tree, install_lock(tree):
        journal = _exists_read(tree, JOURNAL)
        if journal is None:
            if tree.inspect(MARKER) is not None:
                raise ValidationError("Marker has no journal; restore from verified backup")
            return {"status": "NO_PENDING_INSTALL"}
        rollback(tree, loads(journal.decode()))
        return {"status": "ROLLED_BACK"}


def install(source, destination, expected_digest, mode="install", conflict="error", overrides=None,
            dry_run=False, fail_after=None, *, codeowner="@maintainer", rules_observation=None,
            expected_rules_observation_sha256=None, default_branch=None, review_app_id=None,
            configure=False, discover=True, propose_operating_capacity=False):
    validate_codeowner(codeowner)
    if mode not in {"install", "upgrade"} or conflict not in {"error", "backup"}:
        raise ValidationError("Only install/upgrade and error/backup are supported; skip was removed")
    if type(propose_operating_capacity) is not bool or (propose_operating_capacity and not configure):
        raise ValidationError("A capacity governance proposal requires explicit configured adoption")
    if not expected_digest or len(expected_digest) != 64:
        raise ValidationError("Provide the externally approved manifest SHA-256")
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValidationError("Source and destination trees must not overlap")
    with Tree(source) as src:
        digest, content = verify_release(src, expected_digest)
        source_manifest_json = src.read(MANIFEST).decode("utf-8")
    planned = {path: data for path, data in content.items() if managed(path)}
    if CODEOWNERS in planned:
        planned[CODEOWNERS] = planned[CODEOWNERS].replace(b"@maintainer", codeowner.encode("ascii"))
    owner_report = {"status": "SEEDED" if CODEOWNERS in planned else "NOT_IN_SOURCE",
                    "path": CODEOWNERS, "requested_owner": codeowner, "project_owned": True,
                    "requested_owner_applied": CODEOWNERS in planned}
    def rules_report():
        config = load_yaml(planned[CONFIG])
        github = config.get("github") if isinstance(config, dict) else None
        repository = github.get("repository") if isinstance(github, dict) else None
        return load_observation_report(rules_observation, expected_rules_observation_sha256,
            repository=repository, default_branch=default_branch, review_app_id=review_app_id)
    configuration_report = None
    governance_proposal = None
    ignore_report = {"status": "NOT_IN_SOURCE", "path": GITIGNORE, "project_owned": True,
                     "audit_policy": "Version .agentic-state/operating/changes/; review existing project ignore rules if they hide it."}
    def ignore_plan(existing_ignore=None):
        if GITIGNORE_TEMPLATE in content:
            planned[GITIGNORE] = merge_operating_ignores(existing_ignore, content[GITIGNORE_TEMPLATE])
            ignore_report.update(status="SEEDED" if existing_ignore is None else "UNCHANGED" if planned[GITIGNORE] == existing_ignore else "MERGED",
                previous_sha256=None if existing_ignore is None else sha256(existing_ignore),
                proposed_sha256=sha256(planned[GITIGNORE]))
    def configure_plan(existing_config=None, existing_receipt=None):
        nonlocal configuration_report, governance_proposal
        from .adoption_config import prepare_config, operating_capacity_proposal
        from .contracts import Contracts
        from .policy import inspect_config
        existing = load_yaml(existing_config) if existing_config is not None else None
        result = prepare_config(load_yaml(planned[CONFIG]), existing=existing,
            receipt_project_id=(existing_receipt or {}).get("project_id"), project_root=destination,
            overrides=overrides, codeowner=codeowner, discover=discover)
        cfg = result.pop("config")
        cfg, governance_proposal = operating_capacity_proposal(cfg, existing=existing_config is not None,
            requested=propose_operating_capacity, dry_run=dry_run)
        planned[CONFIG] = existing_config if existing == cfg else json_bytes(cfg)
        inspection = inspect_config(cfg, load_yaml(content[".agentic/workflow.yaml"]), Contracts(source / ".agentic/schemas"))
        configuration_report = {**inspection, "discovery": result}
    if mode == "install" and overrides and not configure:
        cfg = load_yaml(planned[CONFIG])
        cfg["project"].update({key: value for key, value in overrides.items() if key in {"name", "short_name"}})
        if "repository" in overrides:
            cfg["github"]["repository"] = overrides["repository"]
        if "jira_key" in overrides:
            cfg["jira"]["project_key"] = overrides["jira_key"]
        planned[CONFIG] = json_bytes(cfg)
    if dry_run and not destination.exists():
        # Pin the existing parent without creating the destination.
        with Tree(destination.parent):
            if configure:
                configure_plan()
            ignore_plan()
            return {"status": "PLAN", "managed_files": sorted(planned), "source_manifest_sha256": digest,
                    "installed": False, "configuration": configuration_report, "governance_proposal": governance_proposal,
                    "gitignore": ignore_report, "codeowners": owner_report, **rules_report()}
    with Tree(destination, create=not dry_run) as dst:
        if dst.inspect(JOURNAL) is not None or dst.inspect(MARKER) is not None:
            raise ValidationError("An interrupted installation requires --recover")
        existing_config = _exists_read(dst, CONFIG)
        receipt_raw = _exists_read(dst, INSTALLED)
        existing_receipt = loads(receipt_raw.decode()) if receipt_raw is not None else None
        assert_quiescent(existing_config)
        planning_inputs = {CONFIG: existing_config, INSTALLED: receipt_raw}
        if GITIGNORE_TEMPLATE in content:
            planning_inputs[GITIGNORE] = _exists_read(dst, GITIGNORE)
            ignore_plan(planning_inputs[GITIGNORE])
        owner_inputs = {}
        if CODEOWNERS in planned:
            for candidate in (CODEOWNERS, "CODEOWNERS", "docs/CODEOWNERS"):
                existing_owners = _exists_read(dst, candidate)
                # Retain the examined precedence prefix, including absent higher
                # locations. A new root/docs policy must not be silently shadowed.
                owner_inputs[candidate] = existing_owners
                if existing_owners is not None:
                    if candidate == CODEOWNERS:
                        planned[CODEOWNERS] = existing_owners
                    else:
                        # A new .github file would shadow GitHub's lower-priority
                        # root/docs ownership file; preserve that effective policy.
                        del planned[CODEOWNERS]
                    owner_report.update(status="PRESERVED", path=candidate, requested_owner_applied=False,
                                        existing_sha256=sha256(existing_owners))
                    break
        if mode == "upgrade":
            if _exists_read(dst, INSTALLED) is None or existing_config is None:
                raise ValidationError(f"Upgrade requires a same-version {VERSION} installation; migrate older versions through a reviewed install")
            verify_installed(destination)
            planned[CONFIG] = existing_config
        if configure:
            configure_plan(existing_config, existing_receipt)
        original_version = _exists_read(dst, PROVENANCE)
        install_id = str(uuid.uuid4())
        if mode == "upgrade":
            install_id = loads(original_version.decode())["installation"]["install_id"]
        elif configure and existing_receipt:
            install_id = str(uuid.UUID(existing_receipt["install_id"]))
        version = {"template": {"name": "generic-agentic-development-workflow", "version": VERSION, "schema_revision": 3},
                   "installation": {"install_id": install_id, "last_operation": mode, "operation_at": now_text(),
                                    "source_manifest_sha256": digest, "profile": "manual_reference"}}
        planned[PROVENANCE] = json_bytes(version)
        immutable = {path: sha256(data) for path, data in planned.items() if path not in {CONFIG, PROVENANCE, CODEOWNERS, GITIGNORE}}
        installed = {"template_version": VERSION, "source_manifest_sha256": digest, "immutable_files": immutable,
                     "source_manifest_json": source_manifest_json,
                     "initial_config_sha256": sha256(planned[CONFIG]), "mutable_paths": [CONFIG, CODEOWNERS], "install_id": install_id}
        if GITIGNORE in planned:
            installed["mutable_paths"].append(GITIGNORE)
        if configure:
            installed["project_id"] = configuration_report["discovery"]["project_id"]
        elif existing_receipt and "project_id" in existing_receipt:
            installed["project_id"] = existing_receipt["project_id"]
        planned[INSTALLED] = json_bytes(installed)
        originals, conflicts = {}, []
        for path, data in planned.items():
            old = _exists_read(dst, path)  # Pins and validates every existing parent.
            if path in planning_inputs and old != planning_inputs[path]:
                raise ValidationError("Destination changed while preparing adoption: " + path)
            originals[path] = old
            if old is not None and old != data and path != GITIGNORE and not (mode == "upgrade" and path in {CONFIG, PROVENANCE, INSTALLED}):
                conflicts.append(path)
        if conflicts and conflict == "error":
            raise ValidationError("Conflicting files; no managed files written: " + ", ".join(sorted(conflicts)))
        if dry_run:
            return {"status": "PLAN", "managed_files": sorted(planned), "conflicts": conflicts, "source_manifest_sha256": digest,
                    "installed": False, "configuration": configuration_report, "governance_proposal": governance_proposal,
                    "gitignore": ignore_report, "codeowners": owner_report, **rules_report()}
        adoption_rules = rules_report()
        with install_lock(dst):
            # Re-read while locked: another cooperating installer or editor may
            # have changed a leaf between preflight and lock acquisition.
            if dst.inspect(JOURNAL) is not None or dst.inspect(MARKER) is not None:
                raise ValidationError("Another installation changed destination state")
            for path, old in owner_inputs.items():
                if _exists_read(dst, path) != old:
                    raise ValidationError("CODEOWNERS selection changed after preflight; preserve current project ownership and retry planning")
            for path, old in originals.items():
                if _exists_read(dst, path) != old:
                    raise ValidationError("Destination changed after preflight")
            transaction_id = str(uuid.uuid4())
            journal = {"format": "awf-install-journal-1", "transaction_id": transaction_id,
                "files": [{"path": path, "old": base64.b64encode(originals[path]).decode() if originals[path] is not None else None,
                           "new_sha256": sha256(data)} for path, data in sorted(planned.items())]}
            if conflict == "backup":
                for path, old in originals.items():
                    if old is not None and old != planned[path]:
                        dst.write(f".agentic-backup/{transaction_id}/{path}", old)
            dst.write(JOURNAL, json_bytes(journal))
            dst.write(MARKER, json_bytes({"transaction_id": transaction_id}))
            try:
                for count, (path, data) in enumerate(sorted(planned.items()), 1):
                    dst.write(path, data)
                    if fail_after == count:
                        raise OSError("Injected installation failure")
                for path, data in planned.items():
                    if dst.read(path) != data:
                        raise ValidationError(f"Installed byte verification failed: {path}")
                # Journal remains a fail-closed marker until its final removal.
                dst.unlink(MARKER)
                dst.unlink(JOURNAL)
            except Exception:
                rollback(dst, journal)
                raise
        # Operating configuration is project-owned and uses its own recoverable
        # transaction. Never overwrite existing choices with release defaults.
        # An incomplete/invalid operating layer remains explicit residue; the
        # installed child checks below cannot report CONFIGURED without it.
        from .operating import initialize_operating, inspect_operating
        try:
            initialize_operating(destination, load_yaml(planned[CONFIG]))
        except (ValidationError, OSError, ValueError):
            pass  # inspect_operating preserves the precise actionable refusal.
        operating_report = inspect_operating(destination, load_yaml(planned[CONFIG]))
        return {"status": "INSTALLED" if mode == "install" else "UPGRADED", "template_version": VERSION,
                "install_id": install_id, "source_manifest_sha256": digest, "managed_files": len(planned),
                "profile": "manual_reference", "live_automation_enabled": False,
                "live_automation_enabled_scope": "reference controller and installed background services",
                "native_streams_dispatch_owner": "native host coordinator using the project's execution.native_streams policy",
                "installer_launches_agents": False, "configuration": configuration_report,
                "governance_proposal": governance_proposal, "gitignore": ignore_report,
                "operating": operating_report,
                "codeowners": owner_report, **adoption_rules}
