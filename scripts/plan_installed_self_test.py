#!/usr/bin/env python3
"""Produce inert, deterministic installed-test selections in a source process."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import run_windows_unittests as source

ROOT = Path(__file__).resolve().parents[1]

# Installed/release self-tests retain their pre-unit-rebalance nine-shard policy.
# Keep these owners independent of the twelve-shard unit-test allocator.
INSTALLED_SHARDS = (0, 1, 2, 3, 4, 5, 6, 7, 8)
INSTALLED_HEAVY_MODULE_SHARDS = {
    '.agentic/tests/test_adoption_status.py': 0,
    '.agentic/tests/test_awf238_migration_identity.py': 4,
    '.agentic/tests/test_awf238_owner_boundary.py': 8,
    '.agentic/tests/test_awf238_pre_f3.py': 1,
    '.agentic/tests/test_awf238_same_version_identity.py': 8,
    '.agentic/tests/test_awf238_second_look.py': 5,
    '.agentic/tests/test_awf242_settings_pr.py': 2,
    '.agentic/tests/test_awf243_review_regressions.py': 1,
    '.agentic/tests/test_awf244_controller_wiring.py': 5,
    '.agentic/tests/test_awf244_window_regressions.py': 8,
    '.agentic/tests/test_awf307_combined_visibility.py': 2,
    '.agentic/tests/test_awf307_excluded_refusal_proofs.py': 5,
    '.agentic/tests/test_awf307_jira_owner_policy.py': 6,
    '.agentic/tests/test_awf307_jira_production_counts.py': 2,
    '.agentic/tests/test_awf307_jira_whole_repository.py': 2,
    '.agentic/tests/test_awf307_queue_owner_policy.py': 2,
    '.agentic/tests/test_bootstrap_configuration.py': 4,
    '.agentic/tests/test_continuous_controller_planning.py': 4,
    '.agentic/tests/test_host_process.py': 5,
    '.agentic/tests/test_reference_controller_adapter_install.py': 7,
    '.agentic/tests/test_reporting_boundary.py': 2,
    '.agentic/tests/test_reporting_e10_regressions.py': 3,
    '.agentic/tests/test_reporting_e11_regressions.py': 8,
    '.agentic/tests/test_reporting_e12_regressions.py': 1,
    '.agentic/tests/test_reporting_e13_regressions.py': 3,
    '.agentic/tests/test_reporting_e16_regressions.py': 6,
    '.agentic/tests/test_reporting_e17_regressions.py': 5,
    '.agentic/tests/test_reporting_e3_regressions.py': 1,
    '.agentic/tests/test_reporting_e6_regressions.py': 0,
    '.agentic/tests/test_reporting_e7_regressions.py': 3,
    '.agentic/tests/test_reporting_e9_regressions.py': 5,
    '.agentic/tests/test_reporting_owner_admission.py': 4,
    '.agentic/tests/test_reporting_timing.py': 1,
    '.agentic/tests/test_reporting_wiring.py': 2,
    '.agentic/tests/test_upgrade_195.py': 3,
    '.agentic/tests/test_upgrade_195_preservation.py': 0,
    '.agentic/tests/test_upgrade_195_recovery.py': 7,
    '.agentic/tests/test_upgrade_196_budgeting.py': 4,
    '.agentic/tests/test_upgrade_journal.py': 0,
    '.agentic/tests/test_windows_diagnostics.py': 5,
    'scripts/tests/test_ac42_e2e.py': 1,
    'scripts/tests/test_adoptable_source.py': 1,
    'scripts/tests/test_fresh_bootstrap.py': 7,
    'scripts/tests/test_installed_adopter_configuration.py': 3,
    'scripts/tests/test_publish_release.py': 4,
    'scripts/tests/test_self_test_review.py': 3,
}


def allocate_installed(modules):
    """Preserve the fixed nine-shard whole-module owners and split halves."""
    allocation = {}
    for suite, paths in modules.items():
        if len(paths) != len(set(paths)):
            raise ValueError(f'duplicate inventory paths: {suite}')
        shards = {n: [] for n in INSTALLED_SHARDS}
        for path in sorted(p for p in paths if p not in source.EXCLUDED_MODULES):
            if path in source.SPLIT_MODULES:
                owners = source.SPLIT_MODULES[path][0]
            else:
                owner = INSTALLED_HEAVY_MODULE_SHARDS.get(path)
                if owner is None:
                    owner = int.from_bytes(hashlib.sha256(path.encode('utf-8')).digest(), 'big') % len(INSTALLED_SHARDS)
                owners = (owner,)
            for owner in owners:
                shards[owner].append(path)
        allocation[suite] = shards
    return allocation


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
    allocation = allocate_installed({'.agentic/tests': inventory})['.agentic/tests']
    return {
        'format': 'awf-installed-selection-1',
        'allocation_policy': 'windows-unittest-shards-9',
        'source_manifest_sha256': hashlib.sha256(raw).hexdigest(),
        'inventory': inventory,
        'shards': {str(n): allocation[n] for n in INSTALLED_SHARDS},
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
    allocation = allocate_installed({'release': inventory})['release']
    return dict(format='awf-release-selection-1', scope='installed' if installed_root is not None else 'source',
                shard_count=len(INSTALLED_SHARDS), allocation_policy='windows-unittest-shards-9',
                source_manifest_sha256=hashlib.sha256((root / 'MANIFEST.json').read_bytes()).hexdigest(),
                inventory=inventory, shards={str(n): sorted(allocation[n]) for n in INSTALLED_SHARDS},
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
