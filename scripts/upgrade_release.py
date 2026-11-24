"""Derive an upgrade registry from raw local release-tag bytes, without Git writes.

An explicit expected commit binds the owner-selected tag. Local Git verification
does not establish independent hosted-release provenance or publication authority.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import ValidationError
from agentic.canonical import loads, sha256
from agentic.child_process import child_env
from agentic.safeio import run_bounded
from agentic.upgrade import (CURRENT_RECEIPT_SCHEMA, immutable_from_source_manifest,
                             load_known_versions, managed_manifest_sha256)
import publish_release


def verify_historical_release(release, pin):
    """Use the tag's own verifier: the current verifier rejects other versions."""
    code = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agentic.installer import verify_release
from agentic.safeio import Tree
with Tree(Path(sys.argv[2])) as tree:
    verify_release(tree, sys.argv[3], allow_source_checkout=True)
'''
    child = run_bounded([sys.executable, '-B', '-I', '-c', code,
        str(release / '.agentic/lib'), str(release), pin], cwd=release, env=child_env(),
        timeout=120, stdout_limit=4 * 1024 * 1024, stderr_limit=1024 * 1024)
    if child.returncode:
        raise ValueError('Historical release verifier refused: ' + child.stderr.decode(errors='replace'))


def release_entry(repository, tag, expected_commit, target_version, *, release=None):
    if publish_release.RELEASE_TAG.fullmatch(tag) is None:
        raise ValueError('Expected a release version tag')
    commit = publish_release.git(repository, 'rev-parse', tag + '^{commit}').strip()
    if commit != expected_commit:
        raise ValueError('Release tag differs from the expected raw commit')
    if release is None:
        root = Path(tempfile.mkdtemp(prefix='awf-upgrade-registration-'))
        release = publish_release.materialize_commit(repository, commit, root / 'release')
    # Compare the materialization to raw commit membership/bytes, even when the
    # caller supplies a previously materialized release.
    entries = publish_release._tree_entries(repository, commit)
    blobs = publish_release._tree_blobs(repository, entries)
    publish_release._verify_materialized_tree(release, entries, blobs)
    raw = (release / 'MANIFEST.json').read_bytes()
    manifest = json.loads(raw)
    if manifest['template_version'] != tag[1:]:
        raise ValueError('Tag version differs from its manifest')
    verify_historical_release(release, sha256(raw))
    immutable = immutable_from_source_manifest(manifest)
    def schema(relative):
        data = (release / relative).read_bytes()
        return {'id': json.loads(data)['$id'], 'sha256': sha256(data)}
    return {'version': tag[1:],
        'provenance': {'kind': 'source commit', 'repository': 'this-repository',
                       'tag': tag, 'commit': commit},
        'source_manifest_sha256': sha256(raw),
        'source_manifest_base64': base64.b64encode(raw).decode('ascii'),
        'managed_manifest_sha256': managed_manifest_sha256(immutable),
        'managed_file_count': len(immutable), 'receipt_schema': CURRENT_RECEIPT_SCHEMA,
        'configuration_schema': schema('.agentic/schemas/project-config.schema.json'),
        'operating_configuration_schema': schema('.agentic/schemas/operating-config.schema.json'),
        'migration': {'kind': 'version-only', 'to': target_version, 'new_required_settings': []}}


def materialize_registered(repository, registry, version, destination):
    """Authenticate a disposable baseline against the reviewed registry and tag."""
    load_known_versions((json.dumps(registry) + '\n').encode())
    entry = next(item for item in registry['versions'] if item['version'] == version)
    provenance = entry['provenance']
    tag = 'v' + version
    commit = publish_release.git(repository, 'rev-parse', tag + '^{commit}').strip()
    if commit != provenance['commit']:
        raise ValueError('Registered baseline tag differs from its commit')
    release = publish_release.materialize_commit(repository, commit, destination)
    raw = (release / 'MANIFEST.json').read_bytes()
    if sha256(raw) != entry['source_manifest_sha256']:
        raise ValueError('Registered baseline manifest differs')
    verify_historical_release(release, entry['source_manifest_sha256'])
    return release


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--expected-commit', required=True)
    parser.add_argument('--target-version', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    registry_raw = (args.repository / '.agentic/upgrade/known-versions.json').read_bytes()
    load_known_versions(registry_raw)
    registry = loads(registry_raw.decode('utf-8'))
    if registry['target'] != args.tag.removeprefix('v'):
        raise ValueError('Registration must extend the current registry target')
    entry = release_entry(args.repository, args.tag, args.expected_commit, args.target_version)
    registry['versions'].append(entry)
    registry['target'] = args.target_version
    raw = (json.dumps(registry, indent=2, ensure_ascii=False) + '\n').encode()
    load_known_versions(raw)
    if args.output.resolve() == (args.repository / '.agentic/upgrade/known-versions.json').resolve():
        raise ValueError('Prepare a separate successor registry; preserve the active release registry')
    if args.check:
        if args.output.read_bytes() != raw:
            raise ValueError('Successor registry differs from authenticated tag derivation')
    else:
        with args.output.open('xb') as stream:
            stream.write(raw)
    print(json.dumps({key: entry[key] for key in ('version', 'provenance',
        'source_manifest_sha256', 'managed_manifest_sha256', 'managed_file_count')}, sort_keys=True))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, ValidationError, OSError) as error:
        print('Upgrade registration refused: ' + str(error), file=sys.stderr)
        raise SystemExit(2)
