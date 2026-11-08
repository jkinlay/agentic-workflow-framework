#!/usr/bin/env python3
"""Run one of nine deterministic Windows unittest shards.

From the repository root: python -B scripts/run_windows_unittests.py --shard 0
Add --list to guard all allocations and print this shard without importing tests.
Paths are repository-relative POSIX strings. Measured heavy modules have fixed
owners in HEAVY_MODULE_SHARDS; every other module uses the unsigned big-endian
SHA-256 of its UTF-8 path modulo nine. Adding a path cannot move existing paths.
Each shard's paths are sorted by Unicode code point. The single module in
SPLIT_MODULES is the only exception to whole-module
sharding: it is selected in both of its shards, and each of them runs a fixed,
disjoint half of its test methods (the named first half, or everything else).
Each suite uses a fresh interpreter, the repository cwd and its Linux CI
discovery top-level contract. Selection is logged before any child is launched;
discovery and completed module-suite records must match it exactly.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest


SUITES = ('.agentic/tests', 'scripts/tests', 'global/awf/tests',
          'global/awf-portable/tests')
EXPLICIT_TOP = frozenset(SUITES[:2])
SHARDS = (0, 1, 2, 3, 4, 5, 6, 7, 8)
# test_upgrade_matrix exceeded the Windows job budget on Python 3.13.
# It is the only module split by test
# method: shard 1 runs UPGRADE_MATRIX_FIRST_HALF and shard 8 runs the rest
# (about 943 s and 922 s of the 1865 s measured on 3.13 locally;
# Windows 3.12 CI measured 1042 s and 1185 s). Keep the existing halves.
UPGRADE_MATRIX = '.agentic/tests/test_upgrade_matrix.py'
UPGRADE_MATRIX_FIRST_HALF = frozenset({
    'test_complete_ledger_schema_rejects_changed_column_and_missing_index',
    'test_direct_and_chained_real_fixture_upgrades_are_byte_identical',
    'test_fixture_blobs_are_stored_byte_exactly_by_git',
    'test_fixture_storage_is_exact_deduplicated_and_under_ten_mb',
    'test_full_contract_validation_archives_exact_key_critic_counterexample',
    'test_legacy_identification_ignores_installation_level_identifiers',
    'test_materializer_uses_owner_values_comments_and_both_line_endings',
    'test_other_refusals_and_dry_run_use_real_fixture_and_write_nothing',
    'test_real_fixture_modified_managed_file_reports_both_hashes_and_changes_nothing',
    'test_schema_invalid_operating_refuses_before_any_write',
    'test_shipped_194_routing_ledger_migrates_exact_schema_and_preserves_rows',
    'test_shipped_table_matches_every_real_fixture',
    'test_state_compatibility_is_listed_for_every_adjacent_step',
})
SPLIT_MODULES = {UPGRADE_MATRIX: ((1, 8), UPGRADE_MATRIX_FIRST_HALF)}
# Windows 3.12 sampled suite totals (seconds), before reallocation:
# shard 0: agentic 2907 / scripts 482; shard 4: agentic 1378 / scripts >2199
# (cancelled, not a completed duration). Child stderr was buffered per suite:
# its log timestamps cannot identify individual module durations. Isolated
# local module-suite measurements: admission 1318, adoption_status 448-483,
# review_regressions 146, upgrade_journal 613, installed adapter 227, host_process
# 162, publish_release 1682, adoptable_source 213, self_test_review 82, ac42_e2e
# 556, fresh_bootstrap 152; prior local bootstrap_configuration 1115. The
# upgrade_195 log records 181+211+199 seconds in bootstrap alone.
# These fixtures and the review regressions need fixed owners.
# The removed terminal admission module is replaced by settings-via-PR coverage.
# Stacked-suite local observations: awf244 cohort 1334; reporting boundary 627,
# e10 597 / e11 694 / e12 191 / e13 232 / e16 614 / e3 237 / e6 123 / e7 203 /
# e9 383 / owner 206 / wiring 122;
# awf238 owner boundary 662 / pre_f3 386; upgrade preservation 1403 / recovery
# 1640 / budgeting 417. Calendar attribution and its newer window regressions
# (180 s planning allowance, outside that cohort) are estimates, not per-module
# CI measurements. Third-look (900 s) and reporting e17 (300 s) also use
# explicit planning allowances pending measurement. Preassign incoming paths so their
# arrival cannot crowd an existing heavy shard. New module_result records carry
# elapsed seconds even when the parent buffers output; refresh estimates there.
HEAVY_MODULE_SHARDS = {
    '.agentic/tests/test_adoption_status.py': 0,
    '.agentic/tests/test_awf242_settings_pr.py': 2,
    '.agentic/tests/test_awf243_review_regressions.py': 3,
    '.agentic/tests/test_bootstrap_configuration.py': 6,
    '.agentic/tests/test_host_process.py': 5,
    '.agentic/tests/test_reference_controller_adapter_install.py': 7,
    '.agentic/tests/test_upgrade_195.py': 3,
    '.agentic/tests/test_upgrade_journal.py': 0,
    '.agentic/tests/test_windows_diagnostics.py': 5,
    'scripts/tests/test_ac42_e2e.py': 6,
    'scripts/tests/test_adoptable_source.py': 1,
    'scripts/tests/test_fresh_bootstrap.py': 7,
    'scripts/tests/test_publish_release.py': 4,
    'scripts/tests/test_self_test_review.py': 3,
    '.agentic/tests/test_awf238_owner_boundary.py': 8,
    '.agentic/tests/test_awf238_pre_f3.py': 2,
    '.agentic/tests/test_awf238_second_look.py': 6,
    '.agentic/tests/test_awf244_window_regressions.py': 8,
    '.agentic/tests/test_reporting_boundary.py': 2,
    '.agentic/tests/test_reporting_e10_regressions.py': 3,
    '.agentic/tests/test_reporting_e11_regressions.py': 3,
    '.agentic/tests/test_reporting_e12_regressions.py': 1,
    '.agentic/tests/test_reporting_e13_regressions.py': 6,
    '.agentic/tests/test_reporting_e16_regressions.py': 5,
    '.agentic/tests/test_reporting_e3_regressions.py': 1,
    '.agentic/tests/test_reporting_e6_regressions.py': 0,
    '.agentic/tests/test_reporting_e7_regressions.py': 3,
    '.agentic/tests/test_reporting_e9_regressions.py': 5,
    '.agentic/tests/test_reporting_owner_admission.py': 4,
    '.agentic/tests/test_reporting_wiring.py': 2,
    '.agentic/tests/test_upgrade_195_preservation.py': 0,
    '.agentic/tests/test_upgrade_195_recovery.py': 7,
    '.agentic/tests/test_upgrade_196_budgeting.py': 4,
}
ZERO_TEST_MODULES = frozenset({
    '.agentic/tests/test_awf36_automation_fixtures.py',
    # The stacked reporting suite's network/owner fixture helper has no cases.
    '.agentic/tests/test_reporting_fixtures.py',
})
# The installed-runtime acceptance suite has its own CI job and budget.
EXCLUDED_MODULES = frozenset({'scripts/tests/test_installed_self_test.py'})
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic.child_process import child_env  # noqa: E402
from agentic import self_test_shards  # noqa: E402


def inventory(root):
    """Enumerate all test_*.py files, independently of unittest discovery."""
    root = Path(root).resolve()
    modules = {}
    for suite in SUITES:
        start = root / suite
        if not start.is_dir():
            raise ValueError(f'missing suite root: {suite}')
        paths = []
        def walk_error(error):
            raise error
        for directory, _, names in os.walk(start, onerror=walk_error):
            for name in names:
                if name.startswith('test_') and name.endswith('.py'):
                    paths.append((Path(directory) / name).relative_to(root).as_posix())
        if not paths:
            raise ValueError(f'no test modules in suite root: {suite}')
        modules[suite] = sorted(paths)
    return modules


def allocate(modules):
    """Assign fixed heavy owners, otherwise a stable hash of the whole path."""
    allocation = {}
    for suite, paths in modules.items():
        if len(paths) != len(set(paths)):
            raise ValueError(f'duplicate inventory paths: {suite}')
        ordered = sorted(path for path in paths if path not in EXCLUDED_MODULES)
        shards = {shard: [] for shard in SHARDS}
        for path in ordered:
            if path in SPLIT_MODULES:
                for shard in SPLIT_MODULES[path][0]:
                    shards[shard].append(path)
            else:
                shard = HEAVY_MODULE_SHARDS.get(path)
                if shard is None:
                    shard = int.from_bytes(hashlib.sha256(path.encode('utf-8')).digest(), 'big') % len(SHARDS)
                shards[shard].append(path)
        allocation[suite] = {shard: sorted(paths) for shard, paths in shards.items()}
    return allocation


def guard(root, allocation):
    """Re-enumerate disk and reject incomplete, duplicate or invalid ownership."""
    expected = inventory(root)
    if set(allocation) != set(SUITES):
        raise ValueError('suite allocations do not match suite roots')
    for suite in SUITES:
        shards = allocation[suite]
        if (set(shards) != set(SHARDS)
                or any(type(index) is not int for index in shards)):
            raise ValueError(f'invalid shard indices: {suite}')
        counts = Counter(path for paths in shards.values() for path in paths)
        duplicate = sorted(path for path, count in counts.items() if count != copies(path))
        misplaced = sorted(path for path in SPLIT_MODULES if path in counts
                           and {index for index, paths in shards.items() if path in paths}
                           != set(SPLIT_MODULES[path][0]))
        included = set(expected[suite]) - EXCLUDED_MODULES
        unknown = sorted(set(counts) - included)
        missing = sorted(included - set(counts))
        if duplicate or unknown or missing or misplaced:
            raise ValueError(f'{suite}: duplicate={duplicate}, unknown={unknown}, '
                             f'missing={missing}, misplaced={misplaced}')


def copies(path):
    """A split module is selected once in each of its shards; others once."""
    return len(SPLIT_MODULES[path][0]) if path in SPLIT_MODULES else 1


def flatten(tests):
    for test in tests:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def split_cases(path, tests, shard):
    """Return this shard's fixed, disjoint half of a split module's cases."""
    shards, first_half = SPLIT_MODULES[path]
    cases = sorted(flatten(tests), key=lambda case: case.id())
    if any(type(case).__module__ == 'unittest.loader' for case in cases):
        # Import errors and module-level skips stay visible in both shards.
        return tests
    names = [getattr(case, '_testMethodName', None) for case in cases]
    if len(names) != len(set(names)):
        raise ValueError(f'{path}: split test names are not unique')
    absent = sorted(first_half - set(names))
    if absent:
        raise ValueError(f'{path}: split names not discovered: {absent}')
    if not set(names) - first_half:
        raise ValueError(f'{path}: split second half is empty')
    first = shards.index(shard) == 0
    return unittest.TestSuite([case for case, name in zip(cases, names)
                               if (name in first_half) == first])


def reconcile(selected, executed):
    """Require exactly one execution record for each selected module."""
    if Counter(selected) != Counter(executed) or len(executed) != len(set(executed)):
        raise ValueError(f'execution mismatch: selected={selected}, recorded={executed}')


class RecordedSuite(unittest.TestSuite):
    """Record a module only after its discovered suite has returned."""

    def __init__(self, tests, path, executed):
        super().__init__([tests])
        self.path = path
        self.executed = executed
        self.allowed_empty = path in ZERO_TEST_MODULES and tests.countTestCases() == 0

    def run(self, result, debug=False):
        started = time.monotonic()
        before = (result.testsRun, len(result.errors), len(result.skipped))
        result = super().run(result, debug)
        after = (result.testsRun, len(result.errors), len(result.skipped))
        if before == after and not self.allowed_empty:
            raise ValueError(f'empty execution for selected module: {self.path}')
        self.executed.append(self.path)
        # Parent output is buffered per suite. Carry elapsed time in the record
        # so hosted log timestamps need not be mistaken for module timings.
        print(json.dumps(dict(event='module_result', path=self.path,
                              tests_run=after[0] - before[0],
                              seconds=round(time.monotonic() - started, 3))), flush=True)
        return result


class SelectedLoader(unittest.TestLoader):
    """Filter discovery paths, retaining unittest imports, hooks and fixtures."""

    def __init__(self, root, selected, shard=None):
        super().__init__()
        self.root = root
        self.selected = set(selected)
        self.shard = shard
        self.split_cases = {}
        self.discovered = []
        self.executed = []
        self.zero_test_modules = {}

    def _match_path(self, path, full_path, pattern):
        # TestLoader explicitly provides this seam for alternate matching.
        relative = Path(full_path).relative_to(self.root).as_posix()
        return super()._match_path(path, full_path, pattern) and relative in self.selected

    def _find_test_path(self, full_path, pattern):
        relative = Path(full_path).relative_to(self.root).as_posix()
        if relative in self.selected:
            self_test_shards.load_fixture_prerequisites(self.root, relative)
        # Wrap the suite returned by real discovery, including its import-error
        # or explicit SkipTest cases. Do not guess dotted module names.
        tests, recurse = super()._find_test_path(full_path, pattern)
        if relative in self.selected and tests is not None:
            if tests.countTestCases() == 0:
                if relative not in ZERO_TEST_MODULES:
                    raise ValueError(f'empty discovery for selected module: {relative}')
                self.zero_test_modules[relative] = 0
            if relative in SPLIT_MODULES:
                if self.shard not in SPLIT_MODULES[relative][0]:
                    raise ValueError(f'split module outside its shards: {relative}')
                tests = split_cases(relative, tests, self.shard)
                self.split_cases[relative] = tests.countTestCases()
            self.discovered.append(relative)
            tests = RecordedSuite(tests, relative, self.executed)
        return tests, recurse


def run_suite(root, suite, selected, shard=None):
    """Child entry point: use the suite's existing discovery start/top level."""
    # Match `python -m unittest` from the repository root rather than exposing
    # this script's directory as an extra import location in the child.
    sys.path[0] = str(root)
    loader = SelectedLoader(root, selected, shard)
    if selected:
        kwargs = {'top_level_dir': str(root / suite)} if suite in EXPLICIT_TOP else {}
        tests = loader.discover(str(root / suite), **kwargs)
        result = unittest.TextTestRunner(verbosity=2).run(tests)
        successful = result.wasSuccessful() and not loader.errors
        tests_run = result.testsRun
    else:
        # An empty slice is intentional, unlike a selected module loading zero tests.
        successful, tests_run = True, 0
    reconcile(selected, loader.discovered)
    reconcile(selected, loader.executed)
    print(json.dumps(dict(event='suite_result', suite=suite,
                          discovered=loader.discovered, executed=loader.executed,
                          zero_test_modules=loader.zero_test_modules,
                          split_cases=loader.split_cases,
                          tests_run=tests_run, successful=successful)), flush=True)
    return 0 if successful else 1


def run_shard(root, shard, allocation):
    """Run every suite even after failures, retaining the first nonzero exit."""
    status = 0
    for suite in SUITES:
        selected = allocation[suite][shard]
        command = [sys.executable, '-B', str(Path(__file__).resolve()),
                   '--root', str(root), '--shard', str(shard), '--_suite', suite]
        try:
            child = subprocess.run(command, cwd=root, capture_output=True, text=True,
                                   check=False, env=child_env())
        except OSError as error:
            print(f'{suite}: child launch failed: {error}', file=sys.stderr)
            status = status or 1
            continue
        print(child.stdout, end='', flush=True)
        print(child.stderr, end='', file=sys.stderr, flush=True)
        status = status or child.returncode
        try:
            records = []
            for line in child.stdout.splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get('event') == 'suite_result':
                    records.append(record)
            if len(records) != 1:
                raise ValueError('missing suite_result or duplicate suite_result records')
            record = records[0]
            if record['suite'] != suite:
                raise ValueError('suite_result has the wrong suite')
            reconcile(selected, record['discovered'])
            reconcile(selected, record['executed'])
            if record['successful'] is not True:
                status = status or 1
        except (KeyError, TypeError, ValueError) as error:
            print(f'{suite}: {error}', file=sys.stderr)
            status = status or 1
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT, help='repository root')
    parser.add_argument('--shard', type=int, choices=SHARDS, required=True)
    parser.add_argument('--list', action='store_true', help='guard and list only; no suite execution')
    parser.add_argument('--_suite', choices=SUITES, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        allocation = allocate(inventory(root))
        guard(root, allocation)
        if args._suite:
            return run_suite(root, args._suite, allocation[args._suite][args.shard], args.shard)
        print(json.dumps(dict(event='selected', shard=args.shard,
                              excluded=sorted(EXCLUDED_MODULES),
                              split={path: dict(shards=list(SPLIT_MODULES[path][0]),
                                                half=SPLIT_MODULES[path][0].index(args.shard))
                                     for suite in SUITES for path in allocation[suite][args.shard]
                                     if path in SPLIT_MODULES},
                              modules={suite: allocation[suite][args.shard] for suite in SUITES})),
              flush=True)
        if args.list:
            return 0
        return run_shard(root, args.shard, allocation)
    except (OSError, ValueError, ImportError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
