#!/usr/bin/env python3
"""Exercise real, offline Git autocrlf roundtrips without touching user Git state.

API: run_validation(source, expected_manifest_sha256, workdir,
                    python_executable=None) -> dict

The helper retains its uniquely named fixture directory and command logs. Every
Git command uses isolated configuration, an empty hooks/template directory and
local-file-only transport. Git checkout bytes are never repaired or rehashed to
make a failed release pass. A negative fixture overrides LF attributes solely in
its disposable .git/info/attributes, leaving the original tracked payload intact.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True
WORKFLOW = ".agentic/scripts/workflow.py"


class GitCheckoutValidationError(RuntimeError):
    def __init__(self, message, report):
        super().__init__(message)
        self.report = report


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def _pairs(items):
    value = {}
    for key, child in items:
        require(key not in value, f"Duplicate JSON key: {key}")
        value[key] = child
    return value


def _json(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)


def _files(root):
    """Read ordinary fixture/source files; allow only exact-root Git metadata."""
    result = {}
    for directory, directories, names in os.walk(root, followlinks=False):
        directory = Path(directory)
        for name in [*directories, *names]:
            path = directory / name
            metadata = path.lstat()
            require(not stat.S_ISLNK(metadata.st_mode) and not getattr(metadata, "st_file_attributes", 0) & 0x400,
                    f"Linked or reparse-point validation input: {path}")
            require(stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode), f"Unsupported validation input: {path}")
            if stat.S_ISREG(metadata.st_mode):
                require(metadata.st_nlink == 1, f"Hardlinked validation input: {path}")
        if directory == root:
            if ".git" in directories:
                directories.remove(".git")
            names = [name for name in names if name != ".git"]
        for name in names:
            path = directory / name
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def _copy(files, destination):
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        path = destination.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _managed(name):
    return name.startswith(".agentic/") or name in {"AGENTS.md", ".github/PULL_REQUEST_TEMPLATE.md"}


def _locations(source, workdir, report=None):
    """Check canonical locations before creating any file or directory."""
    source = Path(source).resolve(strict=True)
    workdir = Path(workdir).resolve()
    require(source.is_dir(), "Source release directory does not exist")
    require(not workdir.is_relative_to(source), "Git fixture work directory must be outside the release source")
    if report is not None:
        report = Path(report).resolve()
        require(not report.is_relative_to(source), "Write the regression report outside the release source")
    return source, workdir, report


def run_validation(source, expected_manifest_sha256, workdir, python_executable=None):
    """Return PASS evidence or raise GitCheckoutValidationError with partial evidence."""
    report = {
        "validation": "git-autocrlf-checkout",
        "status": "FAILED",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "expected_manifest_sha256": expected_manifest_sha256,
        "core_autocrlf": "true",
        "network_used": False,
        "user_git_configuration_modified": False,
        "checks": [],
        "limitations": [
            "Real Git local repositories and clones are tested on this host; remote hosting and other operating systems are not exercised.",
            "Installed attributes are deliberately merged only inside the disposable test project. The installer must preserve the real project's policy.",
            "No arbitrary third-party clean/smudge filters, custom working-tree encodings or higher-priority attribute overrides are qualified.",
        ],
    }
    try:
        source, workdir, _ = _locations(source, workdir)
        # A pure preflight regression: both aliases resolve inside the release,
        # and must reject before mkdir/copy/report writes are even reachable.
        alias = source.parent / ".." / source.parent.name / source.name
        rejected = []
        for invalid_work, invalid_report in [(alias, None), (workdir, alias / "report.json")]:
            try:
                _locations(source, invalid_work, invalid_report)
            except ValueError as exc:
                require("outside the release source" in str(exc), "Location guard failed for an unrelated reason")
                rejected.append(str(exc))
        require(len(rejected) == 2, "Aliased source work/report locations were accepted")
        report["checks"].append({"check": "Aliased in-source work/report paths reject before any writes", "status": "PASS", "negative_cases": 2})
        require(re.fullmatch(r"[0-9a-f]{64}", str(expected_manifest_sha256)), "Expected manifest SHA256 must be lowercase hexadecimal")
        payload = _files(source)
        require("MANIFEST.json" in payload and "MANIFEST.md" in payload, "Source is missing release manifests")
        require(digest(payload["MANIFEST.json"]) == expected_manifest_sha256, "Source manifest differs from the independently approved digest")
        manifest = _json(payload["MANIFEST.json"])
        require(set(manifest) == {"format", "template_version", "files"} and manifest["format"] == "awf-manifest-1", "Unsupported release manifest")
        require(set(payload) - {"MANIFEST.json", "MANIFEST.md"} == set(manifest["files"]), "Source manifest membership differs before Git validation")
        require(all(digest(payload[name]) == expected for name, expected in manifest["files"].items()), "Source file bytes differ from the approved manifest before Git validation")
        require(".gitattributes" in payload and ".agentic/templates/installed.gitattributes" in payload,
                "Release and installed-project LF attribute policies are required")
        report["template_version"] = manifest["template_version"]
        report["release_files_checked"] = len(payload)
        report["release_inventory_sha256"] = digest(json.dumps({name: digest(data) for name, data in sorted(payload.items())}, sort_keys=True).encode())
        git = shutil.which("git")
        require(git is not None, "Git executable is required for checkout regression validation")
        python = str(Path(python_executable or sys.executable).absolute())
        workdir.mkdir(parents=True, exist_ok=True)
        run = workdir / ("g" + uuid.uuid4().hex[:10])
        run.mkdir()
        report["run_directory"] = str(run)
        isolated = run / "env"
        isolated.mkdir()
        empty = isolated / "empty"
        empty.write_bytes(b"")
        hooks = isolated / "hooks"
        hooks.mkdir()
        logs = run / "logs"
        logs.mkdir()
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("GIT_") and key.upper() not in {"PYTHONPATH", "PYTHONHOME"}}
        env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_SYSTEM": str(empty), "GIT_CONFIG_GLOBAL": str(empty),
                    "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
                    "GIT_AUTHOR_DATE": "2026-09-11T12:00:00+00:00", "GIT_COMMITTER_DATE": "2026-09-11T12:00:00+00:00",
                    "PYTHONDONTWRITEBYTECODE": "1"})
        configuration = {
            "core.autocrlf": "true", "core.safecrlf": "false", "core.hooksPath": str(hooks),
            "core.attributesFile": str(empty), "core.excludesFile": str(empty), "core.fsmonitor": "false",
            "init.templateDir": str(hooks), "init.defaultBranch": "main", "commit.gpgsign": "false",
            "tag.gpgsign": "false", "user.name": "AWF local Git regression", "user.email": "fixture@example.invalid",
            "protocol.allow": "never", "protocol.file.allow": "always", "submodule.recurse": "false",
            "fetch.recurseSubmodules": "false", "credential.helper": "", "core.askPass": "",
        }
        git_prefix = [git, "--no-pager"]
        for key, value in configuration.items():
            git_prefix.extend(["-c", f"{key}={value}"])

        def command(name, arguments, cwd=run, expected=0):
            started = time.monotonic()
            done = subprocess.run([str(a) for a in arguments], cwd=cwd, env=env, capture_output=True,
                                  timeout=120, check=False)
            index = len(list(logs.iterdir())) + 1
            log = logs / f"{index:03}.log"
            log.write_bytes(done.stdout + b"\n--- stderr ---\n" + done.stderr)
            require(done.returncode == expected if expected is not None else done.returncode != 0,
                    f"{name}: unexpected exit {done.returncode}; inspect {log}")
            report["checks"].append({"check": name, "status": "PASS", "exit_code": done.returncode,
                                     "duration_ms": int((time.monotonic() - started) * 1000),
                                     "log": log.relative_to(run).as_posix()})
            return done

        def git_command(name, arguments, cwd=run):
            return command(name, [*git_prefix, *arguments], cwd)

        def check(name, condition, **evidence):
            require(condition, name)
            report["checks"].append({"check": name, "status": "PASS", **evidence})

        def verify(name, directory, expected=0):
            result = command(name, [python, "-B", WORKFLOW, "verify-installation"], directory, expected)
            if expected == 0:
                _json(result.stdout)
            return result

        def create_repo(name, directory):
            git_command(f"{name}: initialize isolated repository", ["init", directory])
            git_command(f"{name}: stage complete fixture", ["add", "--all", "--force", "--", "."], directory)
            git_command(f"{name}: commit with hooks and signing disabled", ["commit", "--no-gpg-sign", "-m", "Local AWF checkout fixture"], directory)

        def clone_repo(name, repository, destination, no_lf=False):
            git_command(f"{name}: fresh local clone", ["clone", "--local", "--no-hardlinks", "--no-recurse-submodules",
                                                       "--no-checkout", "--", repository, destination])
            if no_lf:
                # Highest-priority, untracked fixture-only override simulates
                # absence of the shipped LF policy without changing any payload.
                information = destination / ".git/info"
                information.mkdir(exist_ok=True)
                (information / "attributes").write_bytes(b"* !text !eol\n")
            git_command(f"{name}: checkout with core.autocrlf=true", ["checkout", "--force", "HEAD"], destination)

        def compare(name, directory, expected_files):
            actual = _files(directory)
            changes = sorted(path for path in set(actual) | set(expected_files) if actual.get(path) != expected_files.get(path))
            check(name, not changes, files_checked=len(expected_files), differing_files=changes,
                  manifest_sha256=digest(actual["MANIFEST.json"]) if "MANIFEST.json" in actual else None)

        invalid_report = run / "invalid-work-report.json"
        helper = source / "scripts/validate_git_checkout.py"
        common = [python, "-B", helper, "--source", source,
                  "--expected-manifest-sha256", expected_manifest_sha256]
        command("CLI rejects an aliased in-source workdir before creating fixtures",
                [*common, "--workdir", alias, "--report", invalid_report], source, expected=2)
        check("Rejected workdir creates no report file", not invalid_report.exists())
        source_report = alias / "awf-invalid-regression-report.json"
        command("CLI rejects an aliased in-source report before any write",
                [*common, "--workdir", run / "unused", "--report", source_report], source, expected=2)
        check("Rejected report path creates neither report nor fixture directory",
              not source_report.exists() and not (run / "unused").exists())
        compare("Rejected aliased CLI paths leave every source file unchanged", source, payload)

        report["git_version"] = git_command("Identify actual Git runtime", ["--version"]).stdout.decode("utf-8").strip()
        verify("Approved source integrity before Git", source)

        root_repo, root_clone = run / "r", run / "rc"
        _copy(payload, root_repo)
        create_repo("Root source", root_repo)
        clone_repo("Root source", root_repo, root_clone)
        compare("Every root-checkout release file retains exact bytes", root_clone, payload)
        check("Root checkout MANIFEST.json retains the approved hash", digest((root_clone / "MANIFEST.json").read_bytes()) == expected_manifest_sha256)
        verify("Root Git checkout integrity CLI passes", root_clone)

        parent_repo, parent_clone = run / "p", run / "pc"
        parent_repo.mkdir()
        # A parent may have a broad, conflicting EOL preference. The release's
        # nested policy must control only its own subtree and retain LF there.
        (parent_repo / ".gitattributes").write_bytes(b"* text eol=crlf\n")
        _copy(payload, parent_repo / "s")
        create_repo("Nested source", parent_repo)
        clone_repo("Nested source", parent_repo, parent_clone)
        compare("Every nested-checkout release file retains exact bytes", parent_clone / "s", payload)
        check("Nested checkout MANIFEST.json retains the approved hash", digest((parent_clone / "s/MANIFEST.json").read_bytes()) == expected_manifest_sha256)
        verify("Nested source Git checkout integrity CLI passes", parent_clone / "s")

        negative = run / "nc"
        clone_repo("No effective LF attributes negative control", root_repo, negative, no_lf=True)
        no_lf = git_command("Negative control has no effective text or EOL attributes",
                            ["check-attr", "text", "eol", "--", "MANIFEST.json"], negative).stdout.decode("utf-8")
        check("Negative control disables both text and EOL policy", "text: unspecified" in no_lf and "eol: unspecified" in no_lf)
        negative_payload = _files(negative)
        changed = sorted(name for name, original in payload.items() if negative_payload.get(name) != original)
        check("Without LF attributes autocrlf changes manifest and payload bytes",
              "MANIFEST.json" in changed and any(name in manifest["files"] for name in changed),
              differing_file_count=len(changed), manifest_sha256=digest(negative_payload["MANIFEST.json"]),
              changed_examples=changed[:8])
        check("Negative manifest mismatch is actual CRLF conversion",
              b"\r\n" in negative_payload["MANIFEST.json"] and negative_payload["MANIFEST.json"].replace(b"\r\n", b"\n") == payload["MANIFEST.json"])
        verify("No-LF checkout is rejected by the integrity CLI", negative, expected=2)

        installed, installed_clone = run / "i", run / "ic"
        installed.mkdir()
        original_attributes = b"# Existing project rule; installation must preserve it.\r\n*.bat text eol=crlf\r\n"
        (installed / ".gitattributes").write_bytes(original_attributes)
        batch_bytes = b"@echo off\r\nrem Existing project batch file\r\n"
        (installed / "existing.bat").write_bytes(batch_bytes)
        command("Install into a project with existing gitattributes", [python, "-B", "scripts/bootstrap_project.py",
                "--dest", installed, "--expected-manifest-sha256", expected_manifest_sha256], source)
        check("Installer preserves existing root gitattributes byte-for-byte", (installed / ".gitattributes").read_bytes() == original_attributes)
        scoped = (installed / ".agentic/templates/installed.gitattributes").read_bytes()
        required_rules = {b"/.agentic/** text eol=lf", b"/AGENTS.md text eol=lf", b"/.github/PULL_REQUEST_TEMPLATE.md text eol=lf"}
        check("Installed merge example supplies every required scoped LF rule", required_rules.issubset(set(scoped.splitlines())))
        merged_attributes = original_attributes + b"\n" + scoped
        (installed / ".gitattributes").write_bytes(merged_attributes)
        check("Disposable manual merge retains the existing policy prefix", merged_attributes.startswith(original_attributes))
        verify("Installed fixture integrity passes before Git", installed)
        expected_installed = {name: data for name, data in _files(installed).items() if _managed(name)}
        require(".agentic/installed-manifest.json" in expected_installed and ".agentic/workflow-version.yaml" in expected_installed,
                "Installed manifest/provenance missing from comparison inventory")
        create_repo("Installed project", installed)
        clone_repo("Installed project", installed, installed_clone)
        actual_installed = {name: data for name, data in _files(installed_clone).items() if _managed(name)}
        installed_changes = sorted(name for name in set(expected_installed) | set(actual_installed)
                                   if expected_installed.get(name) != actual_installed.get(name))
        check("Every installed managed file survives autocrlf unchanged", not installed_changes,
              files_checked=len(expected_installed), differing_files=installed_changes,
              installed_manifest_sha256=digest(actual_installed[".agentic/installed-manifest.json"]))
        batch_attr = git_command("Existing batch policy remains effective", ["check-attr", "text", "eol", "--", "existing.bat"], installed_clone).stdout.decode("utf-8")
        check("Existing batch file remains CRLF and retains its effective rule",
              (installed_clone / "existing.bat").read_bytes() == batch_bytes and "text: set" in batch_attr and "eol: crlf" in batch_attr)
        check("Git retains all manually merged project attribute rules",
              (installed_clone / ".gitattributes").read_bytes().replace(b"\r\n", b"\n") == merged_attributes.replace(b"\r\n", b"\n"))
        verify("Installed Git checkout integrity CLI passes", installed_clone)

        dirty = run / "d"
        _copy(payload, dirty)
        target = dirty / "scripts/validate_git_checkout.py"
        original = target.read_bytes()
        require(b"\n" in original and b"\r\n" not in original, "Builder negative fixture must begin with LF source")
        target.write_bytes(original.replace(b"\n", b"\r\n"))
        manifest_before = (dirty / "MANIFEST.json").read_bytes()
        advisory_before = (dirty / "MANIFEST.md").read_bytes()
        rejection = command("Builder rejects noncanonical CRLF before writing manifests", [python, "-B", "scripts/build_release.py", "--manifest-only"], dirty, expected=None)
        reason = (rejection.stdout + rejection.stderr).decode("utf-8", errors="replace").lower()
        check("Builder rejection specifically identifies line endings", "crlf" in reason or "line ending" in reason
              or "noncanonical" in reason or "must use lf without a bom" in reason)
        check("CRLF rejection leaves both existing manifests unchanged", (dirty / "MANIFEST.json").read_bytes() == manifest_before
              and (dirty / "MANIFEST.md").read_bytes() == advisory_before)

        extra = run / "x"
        _copy(payload, extra)
        (extra / ".agentic/unexpected.txt").write_bytes(b"Unexpected source payload\n")
        verify("Untracked non-Git source payload remains rejected", extra, expected=2)
        nested_git = run / "ng"
        _copy(payload, nested_git)
        (nested_git / ".agentic/.git").mkdir()
        (nested_git / ".agentic/.git/config").write_bytes(b"[core]\n")
        verify("Nested Git metadata remains rejected", nested_git, expected=2)
        # Re-read the supplied release to prove the test never changed it.
        compare("Original supplied release remains byte-identical after validation", source, payload)
        report["status"] = "PASS"
        return report
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise GitCheckoutValidationError(str(exc), report) from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--expected-manifest-sha256", required=True)
    parser.add_argument("--workdir", type=Path, required=True, help="Short external directory; unique test fixtures are retained")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        args.source, args.workdir, args.report = _locations(args.source, args.workdir, args.report)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    try:
        report = run_validation(args.source, args.expected_manifest_sha256, args.workdir)
        code = 0
    except GitCheckoutValidationError as exc:
        report = exc.report
        code = 2
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"status": report["status"], "report": str(args.report.absolute()),
                      "checks": len(report["checks"]), "run_directory": report.get("run_directory"),
                      "error": report.get("error")}, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
