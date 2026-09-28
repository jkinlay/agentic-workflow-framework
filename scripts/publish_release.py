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
import zipfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.child_process import child_env
RECORD_PREFIX = "AWF-RELEASE-RECORD: "


class ReleaseError(ValueError):
    pass


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, *, cwd, env=None, text=True):
    result = subprocess.run(command, cwd=cwd, env=child_env(env), capture_output=True,
                            text=text, timeout=3600, check=False)
    if result.returncode:
        stdout = result.stdout if text else result.stdout.decode(errors="replace")
        stderr = result.stderr if text else result.stderr.decode(errors="replace")
        raise ReleaseError(f"Command failed ({result.returncode}): {' '.join(map(str, command))}\n{stdout}{stderr}")
    return result


def git(root, *args, text=True):
    return run(["git", *args], cwd=root, text=text).stdout


def materialize_commit(repository, commit, destination):
    archive = destination.parent / (destination.name + ".zip")
    run(["git", "archive", "--format=zip", "--output", str(archive), commit], cwd=repository)
    destination.mkdir()
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            name = info.filename.rstrip("/")
            parts = name.split("/")
            if (not name or any(part in ("", ".", "..") for part in parts)
                    or "\\" in info.filename or ":" in info.filename):
                raise ReleaseError("Git archive contains an unsafe member")
            if info.is_dir():
                continue
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(package.read(info))
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
        before = {}
        for name in ("MANIFEST.json", "MANIFEST.md"):
            try:
                before[name] = (source / name).read_bytes()
            except OSError as exc:
                before[name] = None
                problems.append(f"stale generated file: {name}: {exc}")
        result = subprocess.run([sys.executable, "-B", str(source / "scripts/build_release.py"), "--manifest-only"],
                                cwd=source, env=child_env(), capture_output=True, text=True,
                                timeout=300, check=False)
        if result.returncode:
            problems.append("manifest regeneration failed: " + (result.stderr.strip() or result.stdout.strip()))
        else:
            for name in before:
                if before[name] != (source / name).read_bytes():
                    problems.append("stale generated file: " + name)
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
        run(["git", "-c", "tag.gpgSign=false", "tag", "-a", tag, commit,
             "-F", str(tag_file)], cwd=repository)
        run(["git", "push", "origin", f"refs/tags/{tag}"], cwd=repository)
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
