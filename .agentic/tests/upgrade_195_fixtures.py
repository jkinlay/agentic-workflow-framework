"""Published Git bytes and installer-generated receipts, without a second blob corpus.

Local Git-byte verification is not authenticated hosted-release provenance.
Baseline installations here have no runtime and are never CONFIGURED/ACTIVE.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from agentic.child_process import child_env
from agentic.safeio import run_bounded

PUBLISHED_COMMIT = '8ad399943c8f5e4f17db72f3e321b9365ee27782'
PUBLISHED_PIN = '1d1f2fb60d721aa6723a1a99d352bbfda0124224d8c8ba6a2e2977659a003ab7'


def materialize_published(repository, destination, *, commit=PUBLISHED_COMMIT):
    import publish_release
    # Sandboxed fixture readers can have a different SID from the checkout
    # owner. Trust only this explicit repository for these read-only commands;
    # never change global/repository config or accept wildcard safe directories.
    arguments = (*publish_release.RAW_GIT_ARGUMENTS, '-c',
                 'safe.directory=' + Path(repository).resolve().as_posix())
    with patch.object(publish_release, 'RAW_GIT_ARGUMENTS', arguments):
        if commit != PUBLISHED_COMMIT or publish_release.git(repository, 'rev-parse', 'v1.9.5^{commit}').strip() != PUBLISHED_COMMIT:
            raise ValueError('Expected authentic published commit for v1.9.5')
        source = publish_release.materialize_commit(repository, PUBLISHED_COMMIT, destination)
    verify_published(source)
    return source


def materialize_candidate(repository, destination):
    """Project current source; generate manifests only inside the disposable copy.

    Historical bytes/pins and the checkout's generated artifacts stay immutable.
    This development source identity is not final release acceptance.
    """
    import build_release
    from agentic.safeio import Tree
    destination = Path(destination)
    destination.mkdir()
    with Tree(repository) as tree:
        for relative in build_release.release_paths(tree):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(build_release.release_bytes(tree, relative))
        for relative in build_release.SOURCE_CONFIGS.values():
            template = destination / relative
            template.parent.mkdir(parents=True, exist_ok=True)
            template.write_bytes(tree.read(relative))
    result = run_bounded([sys.executable, '-B', str(destination / 'scripts/build_release.py'),
                          '--manifest-only'], cwd=destination, env=child_env(), timeout=120,
                         stdout_limit=4 * 1024 * 1024, stderr_limit=1024 * 1024)
    if result.returncode:
        raise AssertionError('Disposable candidate generation failed: ' + result.stderr.decode('utf-8'))
    return destination


def verify_published(source):
    source = Path(source)
    raw = (source / 'MANIFEST.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != PUBLISHED_PIN:
        raise ValueError('Published 1.9.5 manifest pin mismatch')
    # The published verifier enforces membership and every declared file hash.
    published_call(source, """
from agentic.installer import verify_release
from agentic.safeio import Tree
with Tree(source) as tree:
    verify_release(tree, pin)
print('{}')
""")
    return json.loads(raw)


def published_call(source, code, *arguments):
    prelude = """
import json, sys
from pathlib import Path
source = Path(sys.argv[1])
pin = sys.argv[2]
sys.path.insert(0, str(source / '.agentic/lib'))
"""
    result = run_bounded([sys.executable, '-B', '-c', prelude + code,
                          str(source), PUBLISHED_PIN, *map(str, arguments)],
                         cwd=source, env=child_env(), timeout=120,
                         stdout_limit=4 * 1024 * 1024, stderr_limit=1024 * 1024)
    if result.returncode:
        raise AssertionError('Published installer/verifier failed: ' + result.stderr.decode('utf-8'))
    return json.loads(result.stdout)


def install_baseline(source, project, *, branch='main'):
    verify_published(source)
    return published_call(source, """
from agentic.installer import install, verify_installed
project = Path(sys.argv[3])
result = install(source, project, pin, configure=True, discover=False,
    codeowner='@fixture-owner', overrides={
        'repository': 'example-org/example-repo', 'repository_id': 101,
        'base_branch': sys.argv[4], 'test_command': 'python -m unittest'})
verify_installed(project)
print(json.dumps(result))
""", project, branch)
