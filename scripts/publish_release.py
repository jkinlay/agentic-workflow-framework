#!/usr/bin/env python3
"""Build, draft-publish, or verify a reproducible AWF release."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import NamedTuple
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.child_process import child_env
from agentic import ValidationError
from agentic.safeio import relative_parts
RECORD_PREFIX = "AWF-RELEASE-RECORD: "
RAW_GIT_ARGUMENTS = ("--no-replace-objects", "-c", "core.useReplaceRefs=false")
RAW_GIT_ENV = {"GIT_NO_REPLACE_OBJECTS": "1"}
GENERATORS = (
    ("scripts/generate_contracts.py",),
    ("scripts/generate_examples.py",),
    ("scripts/generate_prompts.py",),
    ("scripts/generate_review_loop.py",),
    ("scripts/generate_interaction.py",),
    ("scripts/build_release.py", "--manifest-only"),
)


class ReleaseError(ValueError):
    pass


class TreeEntry(NamedTuple):
    path: str
    mode: str
    oid: str


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, *, cwd, env=None, text=True, input_data=None):
    result = subprocess.run(command, cwd=cwd, env=child_env(env), input=input_data, capture_output=True,
                            text=text, timeout=3600, check=False)
    if result.returncode:
        stdout = result.stdout if text else result.stdout.decode(errors="replace")
        stderr = result.stderr if text else result.stderr.decode(errors="replace")
        raise ReleaseError(f"Command failed ({result.returncode}): {' '.join(map(str, command))}\n{stdout}{stderr}")
    return result


def git(root, *args, text=True):
    return git_run(root, *args, text=text).stdout


def git_run(root, *args, text=True, input_data=None):
    """Run Git with replacement objects disabled for release identity and bytes."""
    environment = os.environ.copy()
    environment.update(RAW_GIT_ENV)
    return run(["git", *RAW_GIT_ARGUMENTS, *args], cwd=root, env=environment, text=text,
               input_data=input_data)


def _tree_entries(repository, commit):
    """Read and validate the exact raw commit tree before creating any path."""
    resolved = git(repository, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    raw = git_run(repository, "ls-tree", "-rz", "--full-tree", resolved, text=False).stdout
    entries = []
    aliases = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
            path = encoded_path.decode("utf-8")
        except (UnicodeError, ValueError) as exc:
            raise ReleaseError("Git returned a malformed or non-UTF-8 release-tree entry") from exc
        if kind != "blob" or mode not in {"100644", "100755"} or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
            raise ReleaseError(f"Release tree contains a non-regular entry: {path}")
        try:
            parts = relative_parts(path)
        except ValidationError as exc:
            raise ReleaseError(f"Release tree contains an unsafe or nonportable path: {path}") from exc
        # Check every prefix so Foo/a and foo/b cannot name one directory on a
        # case-insensitive filesystem even though their complete paths differ.
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            folded = prefix.casefold()
            if folded in aliases and aliases[folded] != prefix:
                raise ReleaseError(
                    f"Release tree contains case-folding aliases: {aliases[folded]} and {prefix}")
            aliases[folded] = prefix
        entries.append(TreeEntry(path, mode, oid))
    if not entries:
        raise ReleaseError("Release tree is empty")
    return entries


def _tree_blobs(repository, entries):
    oids = list(dict.fromkeys(entry.oid for entry in entries))
    request = b"".join(oid.encode("ascii") + b"\n" for oid in oids)
    raw = git_run(repository, "cat-file", "--batch", text=False, input_data=request).stdout
    blobs = {}
    offset = 0
    for requested in oids:
        newline = raw.find(b"\n", offset)
        if newline < 0:
            raise ReleaseError("Git returned a truncated blob batch")
        try:
            actual, kind, raw_size = raw[offset:newline].decode("ascii").split(" ")
            size = int(raw_size)
        except (UnicodeError, ValueError) as exc:
            raise ReleaseError("Git returned malformed blob metadata") from exc
        offset = newline + 1
        end = offset + size
        if actual != requested or kind != "blob" or size < 0 or end >= len(raw) or raw[end:end + 1] != b"\n":
            raise ReleaseError("Git returned inconsistent release blob data")
        blobs[requested] = raw[offset:end]
        offset = end + 1
    if offset != len(raw):
        raise ReleaseError("Git returned trailing release blob data")
    return blobs


def _verify_materialized_tree(destination, entries, blobs):
    expected = {entry.path: entry for entry in entries}
    expected_directories = {"/".join(entry.path.split("/")[:index])
                            for entry in entries for index in range(1, len(entry.path.split("/")))}
    actual = {}
    actual_directories = set()
    for base, directories, files in os.walk(destination, followlinks=False):
        for name in directories + files:
            node = Path(base) / name
            relative = node.relative_to(destination).as_posix()
            info = node.lstat()
            if node.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ReleaseError("Materialized release contains a link or reparse point: " + relative)
            if name in directories:
                if not stat.S_ISDIR(info.st_mode):
                    raise ReleaseError("Materialized release contains a non-directory: " + relative)
                actual_directories.add(relative)
            else:
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ReleaseError("Materialized release contains a non-regular file: " + relative)
                actual[relative] = info
    if set(actual) != set(expected) or actual_directories != expected_directories:
        raise ReleaseError("Materialized release inventory differs from the raw Git tree")
    for path, entry in expected.items():
        target = destination.joinpath(*path.split("/"))
        if target.read_bytes() != blobs[entry.oid]:
            raise ReleaseError("Materialized release bytes differ from the raw Git tree: " + path)
        if os.name != "nt" and stat.S_IMODE(actual[path].st_mode) != int(entry.mode[-3:], 8):
            raise ReleaseError("Materialized release mode differs from the raw Git tree: " + path)


def materialize_commit(repository, commit, destination):
    """Project one raw commit tree exactly, without archive attributes or replace refs."""
    destination = Path(destination)
    if destination.exists():
        raise ReleaseError("Release materialization destination already exists")
    entries = _tree_entries(repository, commit)
    blobs = _tree_blobs(repository, entries)
    destination.mkdir()
    try:
        for entry in entries:
            target = destination.joinpath(*entry.path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blobs[entry.oid])
            if os.name != "nt":
                target.chmod(int(entry.mode[-3:], 8))
        _verify_materialized_tree(destination, entries, blobs)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination


def version_problems(source):
    problems = []
    try:
        version = (source / "VERSION").read_text(encoding="utf-8").strip()
    except OSError as exc:
        version = None
        problems.append(f"VERSION: {exc}")
    declarations = {}
    patterns = {
        "runtime VERSION": (source / ".agentic/lib/agentic/__init__.py", r'^VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"'),
        "workflow version": (source / ".agentic/workflow-version.yaml", r'"version": "([0-9]+\.[0-9]+\.[0-9]+)"'),
        "README": (source / "README.md", r'Release ([0-9]+\.[0-9]+\.[0-9]+)'),
        "changelog": (source / "CHANGELOG.md", r'^## ([0-9]+\.[0-9]+\.[0-9]+)'),
        "portable skill": (source / "global/awf-portable/SKILL.md", r'Includes AWF ([0-9]+\.[0-9]+\.[0-9]+)'),
        "manifest header": (source / "MANIFEST.json", r'"template_version": "([0-9]+\.[0-9]+\.[0-9]+)"'),
    }
    for label, (path, pattern) in patterns.items():
        try:
            match = re.search(pattern, path.read_text(encoding="utf-8"), re.M)
            declarations[label] = match.group(1) if match else None
        except (OSError, UnicodeError):
            declarations[label] = None
    for label, value in declarations.items():
        if value != version:
            problems.append(f"version disagreement: {label}={value!r}, VERSION={version!r}")
    try:
        runtime = (source / ".agentic/lib/agentic/__init__.py").read_text(encoding="utf-8")
        schema_match = re.search(r'^SCHEMA_REVISION = ([0-9]+)$', runtime, re.M)
        schema_revision = int(schema_match.group(1)) if schema_match else None
    except (OSError, UnicodeError) as exc:
        schema_revision = None
        problems.append(f"runtime schema constant unreadable: {exc}")
    try:
        workflow = json.loads((source / ".agentic/workflow-version.yaml").read_text(encoding="utf-8"))
        workflow_revision = workflow.get("template", {}).get("schema_revision")
    except (OSError, UnicodeError, ValueError) as exc:
        workflow_revision = None
        problems.append(f"workflow schema constant unreadable: {exc}")
    if schema_revision is None or schema_revision != workflow_revision:
        problems.append("schema constants disagree with workflow schema_revision")
    for schema in sorted((source / ".agentic/schemas").glob("*.schema.json")):
        try:
            value = json.loads(schema.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            problems.append(f"schema constant unreadable: {schema.relative_to(source).as_posix()}: {exc}")
            continue
        # These operating inputs have their own wire-format revisions.
        independent_revision = schema.name in {
            "operating-config.schema.json", "operating-epics.schema.json"}
        for field in ("schema_version", "version"):
            prop = value.get("properties", {}).get(field, {})
            if (schema_revision is not None and not independent_revision
                    and "const" in prop and prop["const"] != schema_revision):
                problems.append(f"schema constant disagreement: {schema.relative_to(source).as_posix()}#{field}")
    return problems


def generated_file_problems(source):
    """Regenerate every derived source and report the complete changed-path set."""
    def inventory():
        return {path.relative_to(source).as_posix(): path.read_bytes()
                for path in source.rglob("*") if path.is_file()}

    before = inventory()
    problems = []
    for arguments in GENERATORS:
        script = source / arguments[0]
        result = subprocess.run([sys.executable, "-B", str(script), *arguments[1:]],
                                cwd=source, env=child_env(), capture_output=True, text=True,
                                timeout=300, check=False)
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "no diagnostic"
            problems.append(f"generated source refresh failed: {arguments[0]}: {detail}")
    after = inventory()
    for name in sorted(set(before) | set(after)):
        if before.get(name) != after.get(name):
            problems.append("stale generated file: " + name)
    return problems


def source_report(repository, commit):
    repository = Path(repository).resolve()
    problems = []
    resolved = git(repository, "rev-parse", f"{commit}^{{commit}}").strip()
    head = git(repository, "rev-parse", "HEAD").strip()
    if resolved != head:
        problems.append(f"requested commit {resolved} is not current HEAD {head}")
    raw = git(repository, "status", "--porcelain=v1", "-z", "--untracked-files=all", text=False)
    entries = [item.decode("utf-8", errors="surrogateescape") for item in raw.split(b"\0") if item]
    tracked = sorted(item for item in entries if not item.startswith("?? "))
    untracked = sorted(item[3:] for item in entries if item.startswith("?? "))
    problems.extend("tracked modification: " + item for item in tracked)
    problems.extend("untracked file: " + item for item in untracked)
    problems.extend(version_problems(repository))
    with tempfile.TemporaryDirectory(prefix="awf-release-inspect-") as raw_temp:
        source = materialize_commit(repository, resolved, Path(raw_temp) / "source")
        problems.extend(version_problems(source))
        problems.extend(generated_file_problems(source))
    return {"commit": resolved, "head": head, "problems": problems,
            "tracked_modifications": tracked, "untracked_files": untracked}


def verify_windows_check(path, expected, commit, version):
    raw = Path(path).read_bytes()
    if not re.fullmatch(r"[0-9a-f]{64}", expected or "") or hashlib.sha256(raw).hexdigest() != expected:
        raise ReleaseError("clean-Windows portable check does not match its independent SHA-256 pin")
    value = json.loads(raw.decode("utf-8"))
    required = {"portable_build": "PASS", "host_skill_install": "PASS", "host_skill_verify": "PASS"}
    if (value.get("format") != "awf-clean-windows-portable-check-1" or value.get("status") != "PASS"
            or value.get("version") != version or value.get("source_commit") != commit
            or value.get("checks") != required):
        raise ReleaseError("clean-Windows portable build/install/verify check is absent, incomplete, or for another commit")
    return {"sha256": expected, "checks": required}


def build_assets(source, output, epoch):
    output.mkdir(parents=True, exist_ok=False)
    env = os.environ.copy()
    env.update(SOURCE_DATE_EPOCH=str(epoch), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    line = (source / "VERSION").read_text(encoding="utf-8").strip().removesuffix(".0")
    source_zip = output / f"agentic-workflow-template-v{line}.zip"
    source_result = run([sys.executable, "-B", str(source / "scripts/build_release.py"),
                         "--output", str(source_zip)], cwd=source, env=env)
    run([sys.executable, "-B", str(source / "scripts/build_skill_distribution.py"),
         "--skill-source", str(source / "global/awf-portable"), "--output-dir", str(output / "portable")],
        cwd=source, env=env)
    assets = [source_zip, output / "portable" / f"AWF-SKILL-v{line}.zip",
              output / "portable" / f"AWF-v{line}-distribution.zip"]
    return assets, json.loads(source_result.stdout)


def default_validations(source, output, env):
    report = output / "self-test.json"
    run([sys.executable, "-B", str(source / "scripts/self_test.py"), "--report", str(report)], cwd=source, env=env)
    hygiene = run([sys.executable, "-B", str(source / "scripts/release_hygiene.py")], cwd=source, env=env)
    suites = {}
    for label, directory in (("portable_core", source / "global/awf/tests"),
                             ("portable_host", source / "global/awf-portable/tests")):
        result = run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(directory)], cwd=source, env=env)
        match = re.search(r"Ran ([0-9]+) tests?", result.stderr + result.stdout)
        suites[label] = int(match.group(1)) if match else None
    self_test = json.loads(report.read_text(encoding="utf-8"))
    return {"self_test": self_test.get("tests", {}), "release_hygiene": json.loads(hygiene.stdout),
            "portable_suites": suites}


def changelog_notes(source, version):
    text = (source / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}\b.*?(?=^## |\Z)", text, re.M | re.S)
    if not match:
        raise ReleaseError("CHANGELOG has no notes for the release version")
    return match.group(0).strip()


def render_record(commit, manifest_sha256, assets, validations, windows_check):
    return {"format": "awf-release-record-1", "commit": commit,
            "manifest_sha256": manifest_sha256,
            "assets": {path.name: sha256(path) for path in sorted(assets)},
            "validations": validations, "clean_windows_check": windows_check}


def publish(repository, commit, output_dir, windows_check, windows_check_sha256,
            *, dry_run=False, validation_runner=default_validations, gh="gh"):
    repository = Path(repository).resolve()
    output_path = Path(output_dir).resolve()
    if output_path == repository or output_path.is_relative_to(repository) or output_path.exists():
        raise ReleaseError("output directory must be new and outside the release repository")
    report = source_report(repository, commit)
    if report["problems"]:
        raise ReleaseError("Release source is not publishable:\n" + "\n".join(report["problems"]))
    commit = report["commit"]
    epoch = int(git(repository, "show", "-s", "--format=%ct", commit).strip())
    with tempfile.TemporaryDirectory(prefix="awf-release-publish-") as raw_temp:
        source = materialize_commit(repository, commit, Path(raw_temp) / "source")
        version = (source / "VERSION").read_text(encoding="utf-8").strip()
        windows = verify_windows_check(windows_check, windows_check_sha256, commit, version)
        output = output_path
        assets, source_proof = build_assets(source, output, epoch)
        env = os.environ.copy()
        env.update(SOURCE_DATE_EPOCH=str(epoch), TMP=str(Path(raw_temp) / "tmp"),
                   TEMP=str(Path(raw_temp) / "tmp"), PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
        Path(env["TMP"]).mkdir()
        validations = validation_runner(source, output, env)
        record = render_record(commit, source_proof["manifest_sha256"], assets, validations, windows)
        tag = "v" + version
        tag_message = f"AWF {version}\n\n{RECORD_PREFIX}{json.dumps(record, sort_keys=True, separators=(',', ':'))}\n"
        body = (changelog_notes(source, version) + "\n\n### Reproducible release record\n\n"
                + "```json\n" + json.dumps(record, indent=2, sort_keys=True) + "\n```\n\n"
                + "This release is a draft. The owner publishes it after review.\n")
        result = {"status": "DRY_RUN" if dry_run else "DRAFT_CREATED", "tag": tag,
                  "record": record, "tag_message": tag_message, "release_body": body,
                  "assets": [str(path) for path in assets], "remote_changes": not dry_run}
        if dry_run:
            return result
        tag_file = output / "tag-message.txt"
        body_file = output / "release-body.md"
        tag_file.write_text(tag_message, encoding="utf-8", newline="\n")
        body_file.write_text(body, encoding="utf-8", newline="\n")
        git_run(repository, "-c", "tag.gpgSign=false", "tag", "-a", tag, commit,
                "-F", str(tag_file))
        git_run(repository, "push", "origin", f"refs/tags/{tag}")
        run([gh, "release", "create", tag, *map(str, assets), "--draft", "--verify-tag",
             "--title", f"AWF {version}", "--notes-file", str(body_file)], cwd=repository)
        return result


def tag_record(repository, tag):
    body = git(repository, "for-each-ref", f"refs/tags/{tag}", "--format=%(contents)")
    line = next((item[len(RECORD_PREFIX):] for item in body.splitlines() if item.startswith(RECORD_PREFIX)), None)
    if line is None:
        raise ReleaseError("annotated tag lacks the AWF release record")
    return json.loads(line)


def verify_tag(repository, tag, output_dir, *, gh="gh"):
    repository = Path(repository).resolve()
    output_dir = Path(output_dir).resolve()
    if output_dir == repository or output_dir.is_relative_to(repository) or output_dir.exists():
        raise ReleaseError("verification output directory must be new and outside the repository")
    record = tag_record(repository, tag)
    commit = git(repository, "rev-list", "-n", "1", tag).strip()
    if record.get("commit") != commit:
        raise ReleaseError("tag target differs from its release record")
    epoch = int(git(repository, "show", "-s", "--format=%ct", commit).strip())
    with tempfile.TemporaryDirectory(prefix="awf-release-verify-") as raw_temp:
        base = Path(raw_temp)
        source = materialize_commit(repository, commit, base / "source")
        rebuilt, proof = build_assets(source, output_dir, epoch)
        hashes = {path.name: sha256(path) for path in rebuilt}
        if hashes != record.get("assets") or proof["manifest_sha256"] != record.get("manifest_sha256"):
            raise ReleaseError("rebuilt assets or manifest differ from the annotated tag record")
        published = base / "published"
        published.mkdir()
        run([gh, "release", "download", tag, "--dir", str(published)], cwd=repository)
        observed = {}
        for path in published.iterdir():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ReleaseError("published release assets differ from the annotated tag record")
            observed[path.name] = sha256(path)
        if observed != hashes:
            raise ReleaseError("published release assets differ from the annotated tag record")
        return {"status": "PASS", "tag": tag, "commit": commit,
                "manifest_sha256": proof["manifest_sha256"], "assets": hashes}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    verify = sub.add_parser("verify-release")
    verify.add_argument("--tag", required=True)
    verify.add_argument("--repository", type=Path, default=Path.cwd())
    verify.add_argument("--output-dir", type=Path, required=True)
    verify.add_argument("--gh", default="gh")
    parser.add_argument("--commit")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--clean-windows-check", type=Path)
    parser.add_argument("--expected-clean-windows-check-sha256")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gh", default="gh")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-release":
            result = verify_tag(args.repository, args.tag, args.output_dir, gh=args.gh)
        else:
            if not all((args.commit, args.output_dir, args.clean_windows_check,
                        args.expected_clean_windows_check_sha256)):
                parser.error("publication requires --commit, --output-dir, --clean-windows-check and its SHA-256 pin")
            result = publish(args.repository, args.commit, args.output_dir, args.clean_windows_check,
                             args.expected_clean_windows_check_sha256, dry_run=args.dry_run, gh=args.gh)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ReleaseError, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(json.dumps({"status": "REJECTED", "reason": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
