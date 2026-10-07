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
AWF_SOURCE_REPOSITORY = (ROOT / "MANIFEST.json").is_file()
SOURCE_ONLY_REASON = "needs AWF source-repository files absent from installed projects"


def skip_unless_source_repo(reason: str = SOURCE_ONLY_REASON, *paths: str):
    """Skip unless this is the AWF source repository and every named path exists."""
    present = AWF_SOURCE_REPOSITORY and all((ROOT / path).exists() for path in paths)
    return unittest.skipUnless(present, reason)
