#!/usr/bin/env python3
"""Produce inert, deterministic installed-test selections in a source process."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import run_windows_unittests as source

ROOT = Path(__file__).resolve().parents[1]


def build_plan(root=ROOT):
    root = Path(root).resolve()
    raw = (root / 'MANIFEST.json').read_bytes()
    manifest = json.loads(raw)
    inventory = sorted(p for p in manifest['files']
                       if p.startswith('.agentic/tests/')
                       and Path(p).name.startswith('test_') and p.endswith('.py'))
    actual = sorted(p.relative_to(root).as_posix()
                    for p in (root / '.agentic/tests').rglob('test_*.py'))
    if inventory != actual:
        raise ValueError('installed test inventory differs from candidate manifest')
    allocation = source.allocate({'.agentic/tests': inventory})['.agentic/tests']
    return {
        'format': 'awf-installed-selection-1',
        'allocation_policy': 'windows-unittest-shards-9',
        'source_manifest_sha256': hashlib.sha256(raw).hexdigest(),
        'inventory': inventory,
        'shards': {str(n): allocation[n] for n in source.SHARDS},
        'split': {p: {'shards': list(owners), 'first_half': sorted(names)}
                  for p, (owners, names) in source.SPLIT_MODULES.items()},
        'zero_test_modules': sorted(source.ZERO_TEST_MODULES & set(inventory)),
    }


def encode(plan):
    return (json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=True) + '\n').encode('ascii')


def build_release_plan(root=ROOT, installed_root=None):
    """Complete release inventory using the existing CI owners and split halves.

    The version-1 hosted selection above deliberately remains byte compatible.
    CI's one excluded harness is reconciled against separate installed acceptance.
    """
    from agentic.self_test_shards import (INSTALLED_ACCEPTANCE_COVERAGE,
                                         installed_inventory, source_inventory)
    root = Path(root).resolve()
    inventory = (installed_inventory(installed_root) if installed_root is not None
                 else source_inventory(root))
    if installed_root is not None:
        expected = [p for p in source_inventory(root) if p.startswith('.agentic/tests/')]
        if inventory != expected:
            raise ValueError('installed test inventory differs from source candidate')
    if set(source.EXCLUDED_MODULES) != set(INSTALLED_ACCEPTANCE_COVERAGE):
        raise ValueError('CI exclusions differ from the closed installed acceptance entry')
    allocation = source.allocate({'release': inventory})['release']
    return dict(format='awf-release-selection-1', scope='installed' if installed_root is not None else 'source',
                shard_count=len(source.SHARDS), allocation_policy='windows-unittest-shards-9',
                source_manifest_sha256=hashlib.sha256((root / 'MANIFEST.json').read_bytes()).hexdigest(),
                inventory=inventory, shards={str(n): sorted(allocation[n]) for n in source.SHARDS},
                deferred_coverage={p: reason for p, reason in INSTALLED_ACCEPTANCE_COVERAGE.items() if p in inventory},
                split={p: {'shards': list(owners), 'first_half': sorted(names)}
                       for p, (owners, names) in source.SPLIT_MODULES.items() if p in inventory},
                zero_test_modules=sorted(source.ZERO_TEST_MODULES & set(inventory)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    raw = encode(build_plan(args.root))
    # Handoff and digest files are evidence: never replace previous bytes.
    with args.output.open('xb') as stream:
        stream.write(raw)
    digest = hashlib.sha256(raw).hexdigest()
    with args.output.with_suffix(args.output.suffix + '.sha256').open('x', encoding='ascii') as stream:
        stream.write(digest + '\n')
    print(digest)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
