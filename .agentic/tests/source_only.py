"""Shared gate for tests that need AWF source-repository files.

Installed projects receive only the release MANIFEST members; ``scripts/``,
``tools/``, root documents and historical fixtures exist only in the AWF source
repository. Gate such tests with ``skip_unless_source_repo`` so the installed
self-test reports a reasoned skip instead of an error.
"""
from __future__ import annotations
from pathlib import Path
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


def skip_unless_source_repo(reason: str = SOURCE_ONLY_REASON, *paths: str):
    """Skip unless this is the AWF source repository and every named path exists."""
    present = AWF_SOURCE_REPOSITORY and all((ROOT / path).exists() for path in paths)
    return unittest.skipUnless(present, reason)
