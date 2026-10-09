"""Shared gate for tests that need AWF source-repository files.

Installed projects receive only the release MANIFEST members; ``scripts/``,
``tools/``, root documents and historical fixtures exist only in the AWF source
repository. Gate such tests with ``skip_unless_source_repo`` so the installed
self-test reports a reasoned skip instead of an error.
"""
from __future__ import annotations
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]
INSTALLATION_RECEIPT = ".agentic/installed-manifest.json"
SOURCE_MARKER = "scripts/build_release.py"


def is_awf_source_repository(root: Path) -> bool:
    """True only for an AWF source tree, never for an installed project.

    A root ``MANIFEST.json`` alone is not enough: adopters may own one. Installed
    projects carry the installation receipt and lack the source-only release
    builder.
    """
    return ((root / "MANIFEST.json").is_file() and (root / SOURCE_MARKER).is_file()
            and not (root / INSTALLATION_RECEIPT).exists())


AWF_SOURCE_REPOSITORY = is_awf_source_repository(ROOT)
SOURCE_ONLY_REASON = "needs AWF source-repository files absent from installed projects"
COMMITTED_GIT_REASON = (
    "requires a Git repository with a committed HEAD; "
    "installed commitless runtimes have no source-checkout tuple"
)


def skip_unless_source_repo(reason: str = SOURCE_ONLY_REASON, *paths: str):
    """Skip unless this is the AWF source repository and every named path exists."""
    present = AWF_SOURCE_REPOSITORY and all((ROOT / path).exists() for path in paths)
    return unittest.skipUnless(present, reason)


def committed_git_tuple(root: Path):
    """Return the committed HEAD/tree tuple, or ``None`` for a commitless tree.

    Test modules use this during import, so an installed project's initialized
    but commitless repository must be an ordinary unavailable fixture rather
    than a module-load error.
    """
    values = []
    for revision in ("HEAD", "HEAD^{tree}"):
        try:
            completed = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "--verify", revision],
                capture_output=True, text=True, check=False)
        except OSError:
            return None
        value = completed.stdout.strip()
        if completed.returncode != 0 or len(value) not in (40, 64) or any(
                character not in "0123456789abcdefABCDEF" for character in value):
            return None
        values.append(value.lower())
    return tuple(values)
