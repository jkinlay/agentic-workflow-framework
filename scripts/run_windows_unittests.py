#!/usr/bin/env python3
"""Run one of eight deterministic Windows unittest module shards.

From the repository root: python -B scripts/run_windows_unittests.py --shard 0
Add --list to guard all allocations and print this shard without importing tests.
Paths are repository-relative POSIX strings sorted by Unicode code point, per
suite; index i belongs to base shard i % 6 after the explicit exclusion below,
then each dedicated module below moves to its own shard and the rest of base
shard 1 alternates by position (j % 2) between shards 6 and 7.
Each suite uses a fresh interpreter, the repository cwd and its Linux CI
discovery top-level contract. Selection is logged before any child is launched;
discovery and completed module-suite records must match it exactly.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


SUITES = ('.agentic/tests', 'scripts/tests', 'global/awf/tests',
          'global/awf-portable/tests')
EXPLICIT_TOP = frozenset(SUITES[:2])
SHARDS = (0, 1, 2, 3, 4, 5, 6, 7)
BASE_SHARDS = 6
# Base shard 1 exceeded the 60-minute Windows job budget. test_upgrade_matrix
# (about 33 minutes measured) runs alone in shard 1; the rest of base shard 1
# alternates between shards 6 and 7.
DEDICATED_MODULES = {'.agentic/tests/test_upgrade_matrix.py': 1}
SPLIT_SHARDS = {1: (6, 7)}
ZERO_TEST_MODULES = frozenset({'.agentic/tests/test_awf36_automation_fixtures.py'})
# The installed-runtime acceptance suite has its own CI job and budget.
EXCLUDED_MODULES = frozenset({'scripts/tests/test_installed_self_test.py'})
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

from agentic.child_process import child_env  # noqa: E402


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
    """Allocate independently within each suite using ordinal path ordering."""
    allocation = {}
    for suite, paths in modules.items():
        if len(paths) != len(set(paths)):
            raise ValueError(f'duplicate inventory paths: {suite}')
        ordered = sorted(path for path in paths if path not in EXCLUDED_MODULES)
        shards = {shard: [] for shard in SHARDS}
        for base in range(BASE_SHARDS):
            base_paths = ordered[base::BASE_SHARDS]
            rest = [path for path in base_paths if path not in DEDICATED_MODULES]
            targets = SPLIT_SHARDS.get(base, (base,))
            for position, shard in enumerate(targets):
                shards[shard].extend(rest[position::len(targets)])
        for path in ordered:
            if path in DEDICATED_MODULES:
                shards[DEDICATED_MODULES[path]].append(path)
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
        duplicate = sorted(path for path, count in counts.items() if count != 1)
        included = set(expected[suite]) - EXCLUDED_MODULES
        unknown = sorted(set(counts) - included)
        missing = sorted(included - set(counts))
        if duplicate or unknown or missing:
            raise ValueError(f'{suite}: duplicate={duplicate}, unknown={unknown}, missing={missing}')


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
        before = (result.testsRun, len(result.errors), len(result.skipped))
        result = super().run(result, debug)
        after = (result.testsRun, len(result.errors), len(result.skipped))
        if before == after and not self.allowed_empty:
            raise ValueError(f'empty execution for selected module: {self.path}')
        self.executed.append(self.path)
        return result


class SelectedLoader(unittest.TestLoader):
    """Filter discovery paths, retaining unittest imports, hooks and fixtures."""

    def __init__(self, root, selected):
        super().__init__()
        self.root = root
        self.selected = set(selected)
        self.discovered = []
        self.executed = []
        self.zero_test_modules = {}

    def _match_path(self, path, full_path, pattern):
        # TestLoader explicitly provides this seam for alternate matching.
        relative = Path(full_path).relative_to(self.root).as_posix()
        return super()._match_path(path, full_path, pattern) and relative in self.selected

    def _find_test_path(self, full_path, pattern):
        relative = Path(full_path).relative_to(self.root).as_posix()
        helper = '.agentic/tests/test_awf36_automation_fixtures.py'
        if (relative in self.selected
                and relative == '.agentic/tests/test_awf36_controller_automation.py'
                and (self.root / helper).is_file()):
            # The helper patches the consumer's setUp before discovery loads its
            # cases, even when ordinal allocation puts it in another shard.
            importlib.import_module('test_awf36_automation_fixtures')
        # Wrap the suite returned by real discovery, including its import-error
        # or explicit SkipTest cases. Do not guess dotted module names.
        tests, recurse = super()._find_test_path(full_path, pattern)
        if relative in self.selected and tests is not None:
            if tests.countTestCases() == 0:
                if relative not in ZERO_TEST_MODULES:
                    raise ValueError(f'empty discovery for selected module: {relative}')
                self.zero_test_modules[relative] = 0
            self.discovered.append(relative)
            tests = RecordedSuite(tests, relative, self.executed)
        return tests, recurse


def run_suite(root, suite, selected):
    """Child entry point: use the suite's existing discovery start/top level."""
    # Match `python -m unittest` from the repository root rather than exposing
    # this script's directory as an extra import location in the child.
    sys.path[0] = str(root)
    loader = SelectedLoader(root, selected)
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
            return run_suite(root, args._suite, allocation[args._suite][args.shard])
        print(json.dumps(dict(event='selected', shard=args.shard,
                              excluded=sorted(EXCLUDED_MODULES),
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
