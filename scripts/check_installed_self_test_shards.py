#!/usr/bin/env python3
"""Independently reconcile Windows installed shards against candidate source."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import unittest

from plan_installed_self_test import ROOT, build_plan, encode
from agentic.self_test_shards import COMPONENT_CHECKS, UPGRADE_MATRIX, SHARDS, _unique_object

VERSIONS = ('3.12.10', '3.13.3')


def select_artifacts(folders, run_id):
    """Select the highest attempt per version/shard within this workflow run."""
    if not isinstance(run_id, str) or not re.fullmatch('[1-9][0-9]*', run_id):
        raise ValueError('missing or invalid current run_id')
    pattern = re.compile(r'windows-installed-runtime-self-test-(3[.]12[.]10|3[.]13[.]3)-shard-([0-8])-([1-9][0-9]*)-([1-9][0-9]*)')
    seen, selected = set(), {}
    for folder in folders:
        match = pattern.fullmatch(folder.name)
        if not folder.is_dir() or match is None:
            raise ValueError('unexpected artifact entry or name')
        version, shard, observed_run, attempt = match.groups()
        if observed_run != run_id:
            raise ValueError('artifact run_id differs from this workflow run_id')
        shard, attempt = int(shard), int(attempt)
        key = version, shard
        if (*key, attempt) in seen:
            raise ValueError('duplicate shard within the same attempt')
        seen.add((*key, attempt))
        if key not in selected or attempt > selected[key][0]:
            selected[key] = (attempt, folder)
    return selected


def validate_component_phases(phases, shard):
    if not isinstance(phases, dict) or set(phases) != (set(COMPONENT_CHECKS) if shard == 0 else set()):
        raise ValueError('component phases missing or outside shard zero')
    for phase, evidence in phases.items():
        if (not isinstance(evidence, dict) or set(evidence) != {'count', 'results'}
                or type(evidence['count']) is not int or evidence['count'] != 1
                or not isinstance(evidence['results'], list) or len(evidence['results']) != 1):
            raise ValueError('component phases must execute exactly once')
        result = evidence['results'][0]
        if (not isinstance(result, dict) or set(result) != {'status', 'checks'} or result['status'] != 'PASS'
                or not isinstance(result['checks'], dict) or set(result['checks']) != set(COMPONENT_CHECKS[phase])):
            raise ValueError('component phases have incomplete or failed check results')
        for check in result['checks'].values():
            if not isinstance(check, dict) or set(check) != {'status', 'result'} or check['status'] != 'PASS':
                raise ValueError('component phases have a failed individual check result')


def expected_split_cases(root, plan):
    # Real unittest discovery observes load_tests/inheritance, rather than trusting
    # a report's declared union or counting method text in source.
    tests = root / '.agentic/tests'
    sys.path.insert(0, str(tests))
    from agentic.self_test_shards import flatten
    loader = unittest.TestLoader()
    suite = loader.discover(str(tests), pattern='test_upgrade_matrix.py', top_level_dir=str(tests))
    if loader.errors:
        raise ValueError('candidate split discovery failed')
    cases = list(flatten(suite))
    if any(type(case).__module__ == 'unittest.loader' for case in cases):
        raise ValueError('candidate split is an import error or skip carrier')
    first = set(plan['split'][UPGRADE_MATRIX]['first_half'])
    names = [case._testMethodName for case in cases]
    if len(names) != len(set(names)) or not first <= set(names) or not set(names) - first:
        raise ValueError('candidate split overlap/omission')
    return {n: sorted(case.id() for case in cases if (case._testMethodName in first) == (n == 1)) for n in (1, 8)}


def reconcile(plan, records, partitions):
    """Expected membership and partitions come only from the candidate source."""
    digest = hashlib.sha256(encode(plan)).hexdigest()
    expected_all = sorted(partitions[1] + partitions[8])
    if not partitions[1] or not partitions[8] or len(expected_all) != len(set(expected_all)):
        raise ValueError('candidate split overlaps or is empty')
    indexed = {}
    for version, report in records:
        shard = report.get('shard')
        if version not in VERSIONS or type(shard) is not int or shard not in SHARDS or (version, shard) in indexed:
            raise ValueError('unexpected or duplicate shard')
        indexed[version, shard] = report
    if set(indexed) != {(v, n) for v in VERSIONS for n in SHARDS}:
        raise ValueError('missing shard')
    for version in VERSIONS:
        counts = Counter()
        for shard in SHARDS:
            r = indexed[version, shard]
            if (r.get('scope') != 'installed-shard' or r.get('suite_complete') is not False
                    or r.get('shard_count') != 9 or r.get('status') != 'SHARD_PASS'
                    or r.get('isolation') != 'PASS' or r.get('release_qualified') is not False
                    or r.get('selection_sha256') != digest
                    or r.get('source_manifest_sha256') != plan['source_manifest_sha256']):
                raise ValueError(f'{version}/{shard}: failed shard, isolation or digest binding')
            expected = plan['shards'][str(shard)]
            for key in ('selected_modules', 'discovered_modules', 'completed_modules'):
                if sorted(r.get(key, [])) != expected:
                    raise ValueError(f'{version}/{shard}: {key} mismatch')
            counts.update(r['completed_modules'])
            tests = r.get('tests', {})
            if (type(tests.get('run')) is not int or tests['run'] <= 0
                    or tests.get('failures') != 0 or tests.get('errors') != 0
                    or tests.get('successful') is not True
                    or not isinstance(tests.get('skipped'), list)):
                raise ValueError(f'{version}/{shard}: unsuccessful tests')
            for skip in tests['skipped']:
                if (not isinstance(skip, dict) or not isinstance(skip.get('test'), str) or not skip['test'].strip()
                        or not isinstance(skip.get('reason'), str) or not skip['reason'].strip()):
                    raise ValueError('skip without test and reason')
            validate_component_phases(r.get('component_phases'), shard)
            if r.get('checks', {}).get('integrity') != 'PASS':
                raise ValueError(f'{version}/{shard}: component phases failed')
            if shard in (1, 8):
                if (r.get('split_cases') != {UPGRADE_MATRIX: partitions[shard]}
                        or r.get('split_all_cases') != {UPGRADE_MATRIX: expected_all}):
                    raise ValueError('split overlap/omission or case membership mismatch')
            elif r.get('split_cases') != {} or r.get('split_all_cases') != {}:
                raise ValueError('split execution outside its owners')
            expected_zero = {p: 0 for p in expected if p in plan['zero_test_modules']}
            if r.get('zero_test_modules') != expected_zero:
                raise ValueError('zero-test helper disposition mismatch')
        if counts != Counter({p: 2 if p == UPGRADE_MATRIX else 1 for p in plan['inventory']}):
            raise ValueError('ordinary module executed other than exactly once')
    return {'scope': 'windows-installed-suite', 'status': 'PASS', 'suite_complete': True,
            'selection_sha256': digest, 'source_manifest_sha256': plan['source_manifest_sha256']}


def windows_applicable(event):
    if event.get('event_name') == 'push':
        return True
    if event.get('event_name') == 'pull_request':
        pr = event.get('pull_request', {})
        if type(pr.get('draft')) is not bool:
            raise ValueError('missing draft-event identity')
        return (pr['draft'] is False or
                (pr.get('base', {}).get('ref') == 'main' and
                 pr.get('head', {}).get('ref', '').startswith('release/')))
    raise ValueError('unexpected Windows event')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--matrix-result', required=True)
    parser.add_argument('--event-name', required=True)
    parser.add_argument('--event-file', type=Path, required=True)
    parser.add_argument('--run-id', default=os.environ.get('GITHUB_RUN_ID'))
    args = parser.parse_args(argv)
    try:
        event = json.loads(args.event_file.read_bytes(), object_pairs_hook=_unique_object)
        event['event_name'] = args.event_name
        if not windows_applicable(event):
            if args.matrix_result != 'skipped':
                raise ValueError('inapplicable draft matrix was not skipped')
            print(json.dumps({'status': 'NOT_APPLICABLE', 'reason': 'Windows-inapplicable draft event'}))
            return 0
        if args.matrix_result != 'success':
            raise ValueError('Windows matrix failed or unexpectedly skipped')
        root = args.root.resolve()
        plan = build_plan(root)
        partitions = expected_split_cases(root, plan)
        records = []
        selected = select_artifacts(args.artifacts.iterdir(), args.run_id)
        for (version, shard), (attempt, folder) in sorted(selected.items()):
            report = json.loads((folder / 'self-test.json').read_bytes(), object_pairs_hook=_unique_object)
            if report.get('shard') != shard or type(report['shard']) is not int:
                raise ValueError('artifact shard differs from its report')
            # The matrix interpreter must actually match its artifact version.
            if not str(report.get('python', '')).startswith(version + ' '):
                raise ValueError('interpreter version mismatch')
            if not str(report.get('platform', '')).startswith('Windows-'):
                raise ValueError('report is not from Windows')
            selection = (folder / 'selection.json').read_bytes()
            if selection != encode(plan) or (folder / 'selection.json.sha256').read_text().strip() != hashlib.sha256(selection).hexdigest():
                raise ValueError('artifact selection differs from candidate')
            records.append((version, report))
        result = reconcile(plan, records, partitions)
        result.update(run_id=args.run_id, chosen_attempts={v: {
            str(n): selected[v, n][0] for n in SHARDS} for v in VERSIONS})
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Windows installed-runtime suite FAILED: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
