"""Sequential, fail-closed release self-tests; hosted CI evidence is unchanged."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest

from plan_installed_self_test import build_release_plan, encode
from check_installed_self_test_shards import validate_component_phases
from agentic.self_test_shards import (_unique_object, flatten,
                                     validate_release_selection)
from agentic.child_process import child_env
from agentic.safeio import run_bounded, Tree
from agentic.installer import verify_release
from agentic import ValidationError


class ShardFailure(RuntimeError):
    """A failed attempt has retained its aggregate and available child evidence."""


def run_child(command, *, cwd, env, timeout, log_path):
    """Bound output and kill the owned POSIX group / Windows Job on failure."""
    with Path(log_path).open('xb') as log:
        def retain(channel, data):
            log.write(channel.encode('ascii') + b': ' + data)
            log.flush()
        try:
            return run_bounded(command, cwd=cwd, env=env, timeout=timeout, stream_sink=retain)
        except ValidationError as exc:
            if str(exc).startswith('SUBPROCESS_TIMEOUT:'):
                raise subprocess.TimeoutExpired(command, timeout) from exc
            raise


def strict_report(raw):
    def refuse_constant(value):
        raise ValueError('nonfinite JSON in shard report: ' + value)
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=refuse_constant)


def expected_partitions(root, plan):
    """Discover split cases independently, never trust a child's declared union."""
    result = {}
    for path, spec in plan['split'].items():
        tests = Path(root) / Path(path).parent
        loader = unittest.TestLoader()
        # unittest imports by basename; refuse contamination from another root.
        name = Path(path).stem
        existing = sys.modules.get(name)
        if existing is not None and Path(existing.__file__).resolve() != (Path(root) / path).resolve():
            raise ValueError('split module origin mismatch')
        original_path = sys.path[:]
        try:
            suite = loader.discover(str(tests), pattern=Path(path).name, top_level_dir=str(tests))
            cases = list(flatten(suite))
            if loader.errors or any(type(c).__module__ == 'unittest.loader' for c in cases):
                raise ValueError('split discovery failed')
            names = [c._testMethodName for c in cases]
            first = set(spec['first_half'])
            if len(names) != len(set(names)) or not first <= set(names) or not set(names) - first:
                raise ValueError('split discovery overlap/omission')
            result[path] = {n: sorted(c.id() for c in cases if (c._testMethodName in first) == (n == spec['shards'][0]))
                            for n in spec['shards']}
        finally:
            sys.path[:] = original_path
            if existing is None:
                sys.modules.pop(name, None)
    return result


def validate_report(plan, report, shard, digest, partitions):
    if (not isinstance(report, dict) or report.get('status') != 'SHARD_PASS'
            or report.get('scope') != plan['scope'] + '-shard'
            or type(report.get('shard')) is not int or report['shard'] != shard
            or report.get('shard_count') != plan['shard_count']
            or report.get('suite_complete') is not False or report.get('release_qualified') is not False
            or report.get('selection_sha256') != digest
            or report.get('source_manifest_sha256') != plan['source_manifest_sha256']
            or report.get('checks', {}).get('integrity') != 'PASS'
            or (plan['scope'] == 'installed' and report.get('isolation') != 'PASS')):
        raise ValueError('failed shard identity, integrity or pins')
    expected = plan['shards'][str(shard)]
    for field in ('selected_modules', 'discovered_modules', 'completed_modules'):
        if report.get(field) != expected:
            raise ValueError(field + ': duplicate, foreign or omitted membership')
    validate_component_phases(report.get('component_phases'), shard)
    tests = report.get('tests')
    if (not isinstance(tests, dict) or type(tests.get('run')) is not int or tests['run'] < 0
            or (expected and tests['run'] == 0 and set(expected) - set(plan['zero_test_modules']))
            or type(tests.get('failures')) is not int or tests['failures'] != 0
            or type(tests.get('errors')) is not int or tests['errors'] != 0
            or tests.get('successful') is not True or not isinstance(tests.get('skipped'), list)):
        raise ValueError('unsuccessful tests')
    for skip in tests['skipped']:
        if (not isinstance(skip, dict) or not isinstance(skip.get('test'), str) or not skip['test'].strip()
                or not isinstance(skip.get('reason'), str) or not skip['reason'].strip()):
            raise ValueError('unnamed skip')
    chosen = {p: owners[shard] for p, owners in partitions.items() if shard in owners}
    all_cases = {p: sorted(sum(owners.values(), [])) for p, owners in partitions.items() if shard in owners}
    if report.get('split_cases') != chosen or report.get('split_all_cases') != all_cases:
        raise ValueError('split coverage mismatch')
    if report.get('zero_test_modules') != {p: 0 for p in expected if p in plan['zero_test_modules']}:
        raise ValueError('zero-test disposition mismatch')


def run_self_test(root, report_path, *, timeout, installed_root=None, interpreter=None,
                  review_args=(), env=None, runner=None):
    """Run N children in-process, with a fresh deadline for every child.

    Report/selection paths are exclusive-create evidence. No prior shard is read
    unless it was created by a child of this attempt. No unsharded fallback exists.
    """
    root, report_path = Path(root).resolve(), Path(report_path).resolve()
    target = Path(installed_root).resolve() if installed_root is not None else root
    evidence = report_path.with_name(report_path.name + '.shards')
    if report_path.exists():
        raise FileExistsError('aggregate already exists')
    evidence.mkdir(parents=True, exist_ok=False)
    aggregate = dict(status='FAILED', suite_complete=False, release_qualified=False,
                     current_review={'status': 'NOT_PROVIDED'}, review='NOT_PROVIDED',
                     tests=dict(run=0, failures=0, errors=0, skipped=[], successful=False),
                     coverage_reconciliation={'status': 'INCOMPLETE'},
                     shard_reports=[], deadlines={'per_child_seconds': timeout, 'whole_sequence_seconds': None})
    shard = 0
    try:
        plan = build_release_plan(root, installed_root)
        raw = encode(plan)
        digest = hashlib.sha256(raw).hexdigest()
        selection = evidence / 'selection.json'
        with selection.open('xb') as stream:
            stream.write(raw)
        validate_release_selection(raw, digest, plan['inventory'], plan['source_manifest_sha256'], plan['scope'])
        aggregate.update(scope=plan['scope'] + '-suite', plan_sha256=digest, selection_sha256=digest,
                         shard_count=plan['shard_count'], source_manifest_sha256=plan['source_manifest_sha256'])
        pins = {root / 'MANIFEST.json': (root / 'MANIFEST.json').read_bytes()}
        with Tree(root) as tree:
            _, content = verify_release(tree, plan['source_manifest_sha256'], allow_source_checkout=(root / '.git').exists())
        for path, data in content.items():
            pins[root / path] = data
        pins[selection] = raw
        if installed_root is not None:
            receipt = target / '.agentic/installed-manifest.json'
            pins[receipt] = receipt.read_bytes()
        arguments = list(map(str, review_args))
        for flag in ('--reviews', '--review-required-paths'):
            if flag in arguments:
                index = arguments.index(flag) + 1
                path = Path(arguments[index]).resolve()
                arguments[index] = str(path)
                pins[path] = path.read_bytes()
        def recheck():
            if any(path.read_bytes() != data for path, data in pins.items()):
                raise ValueError('frozen source, review or plan pin changed')
            from agentic.self_test_shards import source_inventory, installed_inventory
            actual = installed_inventory(target) if installed_root is not None else source_inventory(root)
            if actual != plan['inventory']:
                raise ValueError('test inventory changed')
            with Tree(root) as tree:
                verify_release(tree, plan['source_manifest_sha256'], allow_source_checkout=(root / '.git').exists())
        current_review = ({'status': 'NOT_APPLICABLE', 'reason': 'Installed component check; source release review is established separately by archive acceptance'}
                          if installed_root is not None else {'status': 'NOT_PROVIDED'})
        def review():
            if not arguments:
                return current_review
            from release_review import review_source
            def value(flag):
                return arguments[arguments.index(flag) + 1] if flag in arguments else None
            return review_source(root, value('--expected-manifest-sha256'), value('--reviews'),
                                 value('--expected-reviews-sha256'), value('--review-required-paths'),
                                 value('--expected-review-required-paths-sha256'),
                                 allow_source_checkout=(root / '.git').exists())
        current_review = review()
        recheck()
        # Discovery imports only the split module, never the whole test inventory.
        partitions = expected_partitions(root, plan)
        records = []
        for shard in range(plan['shard_count']):
            recheck()
            path = evidence / f'shard-{shard}.json'
            command = [str(interpreter or sys.executable), '-B', *(['-I'] if installed_root is not None else []),
                       str(target / '.agentic/scripts/self_test.py'), '--report', str(path),
                       '--shard', str(shard), '--selection-file', str(selection),
                       '--expected-selection-sha256', digest, *arguments]
            if arguments:
                command.append('--release')
            kwargs = dict(cwd=target, env=child_env(env), timeout=timeout, capture_output=True)
            if runner is None:
                done = run_child(command, cwd=target, env=kwargs['env'], timeout=timeout,
                                 log_path=evidence / f'shard-{shard}.log')
            else:
                done = runner(command, **kwargs)
            recheck()
            if done.returncode:
                raise ValueError(f'child exited {done.returncode}')
            data = path.read_bytes()
            report = strict_report(data)
            validate_report(plan, report, shard, digest, partitions)
            records.append(report)
            aggregate['shard_reports'].append({'shard': shard, 'report': path.name,
                                               'sha256': hashlib.sha256(data).hexdigest(),
                                               'timeout_seconds': timeout})
        recheck()
        if review() != current_review:
            raise ValueError('review changed before aggregation')
        counts = Counter(p for record in records for p in record['completed_modules'])
        if counts != Counter({p: 2 if p in plan['split'] else 1 for p in plan['inventory']}):
            raise ValueError('coverage reconciliation failed')
        for record in records:
            for key in ('run', 'failures', 'errors'):
                aggregate['tests'][key] += record['tests'][key]
            aggregate['tests']['skipped'].extend(record['tests']['skipped'])
        # Re-read immutable child reports before admitting the aggregate.
        for item in aggregate['shard_reports']:
            if hashlib.sha256((evidence / item['report']).read_bytes()).hexdigest() != item['sha256']:
                shard = item['shard']
                raise ValueError('shard report changed')
        aggregate.update(status='PASS', suite_complete=True, current_review=current_review,
                         review=current_review['status'], coverage_reconciliation={'status': 'PASS',
                         'modules': len(plan['inventory']), 'split_cases': partitions},
                         release_qualified=plan['scope'] == 'source' and current_review.get('release_qualified') is True)
        aggregate['tests']['successful'] = True
        recheck()
        if review() != current_review:
            raise ValueError('review changed after aggregation')
        recheck()
    except Exception as exc:
        aggregate.update(status='FAILED', suite_complete=False, release_qualified=False,
                         failing_shard=shard, error=f'shard {shard}: {type(exc).__name__}: {exc}')
        aggregate['tests']['successful'] = False
        aggregate['coverage_reconciliation'] = {'status': 'FAILED', 'failing_shard': shard}
        with report_path.open('xb') as stream:
            stream.write(encode(aggregate))
        raise ShardFailure(aggregate['error']) from exc
    with report_path.open('xb') as stream:
        stream.write(encode(aggregate))
    return aggregate
