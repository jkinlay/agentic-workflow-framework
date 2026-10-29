"""Shared gate for tests that need AWF source-repository files.

Installed projects receive only the release MANIFEST members; ``scripts/``,
``tools/``, root documents and historical fixtures exist only in the AWF source
repository. Gate such tests with ``skip_unless_source_repo`` so the installed
self-test reports a reasoned skip instead of an error.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

LIB = Path(__file__).resolve().parent.parent / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from agentic import VERSION, ValidationError
from agentic.installer import verify_release
from agentic.safeio import Tree
from agentic.operating import (INSTALLED as INSTALLATION_RECEIPT, SOURCE_MARKER,
                               filesystem_reader,
                               is_awf_source_repository as _is_awf_source_repository)

ROOT = Path(__file__).resolve().parents[2]


def write_awf_source_fixture(root: Path) -> None:
    """Build a self-contained AWF source identity in a neutral fixture tree."""
    files = {
        SOURCE_MARKER: b"# AWF source-only fixture\n",
        "OPERATING_CONFIG.yaml": b"{}\n",
    }
    manifest = {
        "files": {
            name: hashlib.sha256(content).hexdigest()
            for name, content in files.items()
        },
        "format": "awf-manifest-1",
        "template_version": VERSION,
    }
    files["MANIFEST.json"] = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    for name, content in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def is_awf_source_repository(root: Path) -> bool:
    """Delegate source identity to the runtime library's canonical predicate."""
    return _is_awf_source_repository(filesystem_reader(root))


AWF_SOURCE_REPOSITORY = is_awf_source_repository(ROOT)
SOURCE_ONLY_REASON = "needs AWF source-repository files absent from installed projects"
COMMITTED_GIT_REASON = (
    "requires a Git repository with a committed HEAD; "
    "commitless installed runtimes and verified closed source archives have no Git tuple"
)


def skip_unless_source_repo(reason: str = SOURCE_ONLY_REASON, *paths: str):
    """Skip unless this is the AWF source repository and every named path exists."""
    present = is_awf_source_repository(ROOT) and all((ROOT / path).exists() for path in paths)
    return unittest.skipUnless(present, reason)


def committed_git_tuple(root: Path):
    """Return the committed HEAD/tree tuple, or ``None`` for a verified unborn HEAD.

    Test modules use this during import, so an installed project's initialized
    but commitless repository and a manifest-verified closed source archive
    have no committed tuple. Every other Git failure is an error: source
    checkouts must not silently skip suites because Git is unavailable, unsafe,
    misconfigured, permission-denied, or corrupt.
    """
    try:
        (root / ".git").lstat()
    except FileNotFoundError:
        if not is_awf_source_repository(root):
            raise RuntimeError("Test tuple requires own Git metadata or a verified closed source archive")
        try:
            with Tree(root) as tree:
                verify_release(tree)
        except (ValidationError, OSError, ValueError) as exc:
            raise RuntimeError("Closed source archive integrity verification failed") from exc
        return None

    def run(*arguments):
        try:
            return subprocess.run(
                ["git", "-C", str(root), *arguments],
                capture_output=True, text=True, check=False)
        except OSError as exc:
            raise RuntimeError(
                "Git execution failed while resolving the committed test tuple"
            ) from exc

    top = run("rev-parse", "--show-toplevel")
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != root.resolve():
        raise RuntimeError(
            "Git does not resolve own test root and does not have a verified unborn HEAD"
        )
    head = run("rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        inside = run("rev-parse", "--is-inside-work-tree")
        symbolic = run("symbolic-ref", "-q", "HEAD")
        branch = symbolic.stdout.strip()
        branch_ref = run("show-ref", "--verify", "--quiet", branch)
        status = run("status", "--porcelain=v1", "--untracked-files=no")
        if (inside.returncode == 0 and inside.stdout.strip() == "true"
                and symbolic.returncode == 0 and branch.startswith("refs/heads/")
                and branch_ref.returncode == 1
                and status.returncode == 0):
            return None
        raise RuntimeError(
            "Git could not resolve HEAD and the repository does not have a verified unborn HEAD"
        )

    values = []
    for revision, completed in (
            ("HEAD", head),
            ("HEAD^{tree}", run("rev-parse", "--verify", "HEAD^{tree}"))):
        value = completed.stdout.strip()
        if completed.returncode != 0 or len(value) not in (40, 64) or any(
                character not in "0123456789abcdefABCDEF" for character in value):
            raise RuntimeError(f"Git returned an invalid committed {revision} object ID")
        values.append(value.lower())
    return tuple(values)
