"""Frozen origin/main outputs; run directly with --freeze-origin-main to record.

The recorder executes the existing planning suites, retaining their assertions
and every generated variant. It captures complete successful return bytes,
graph serialization and Mermaid, plus refusal type/message. No output field is
normalized away. Ordinary tests only read the frozen fixtures.
"""
from contextlib import ExitStack
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
sys.path.insert(0, str(ROOT / '.agentic/tests'))
from agentic import planning, planning_graph
from agentic.canonical import canonical

BASE = '1b589c64ca843707502d5d4460bc899fc4505490'
FIXTURES = ROOT / '.agentic/tests/fixtures/sprint/off'
SUITES = ('test_planning', 'test_planning_graph', 'test_planning_overlap_allowances')
SOURCE_PATHS = (
    '.agentic/lib/agentic/planning.py', '.agentic/lib/agentic/planning_graph.py',
    '.agentic/lib/agentic/operating.py', '.agentic/PROJECT_CONFIG.yaml', 'OPERATING_CONFIG.yaml',
    '.agentic/tests/test_planning.py', '.agentic/tests/test_planning_graph.py',
    '.agentic/tests/test_planning_overlap_allowances.py',
    '.agentic/tests/fixtures/planning/step1.json',
    '.agentic/tests/fixtures/planning_graph/hand_computed.json',
)


def capture():
    events, current, depth = {}, [None], [0]

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            super().startTest(test)
            current[0] = test.id()
            events[current[0]] = []

        def stopTest(self, test):
            current[0] = None
            super().stopTest(test)

    def wrapper(name, function):
        def call(*args, **kwargs):
            outer = depth[0] == 0
            depth[0] += 1
            try:
                value = function(*args, **kwargs)
                if outer and current[0]:
                    output = dict(function=name, result=value)
                    if name == 'planning_graph.build_ticket_plan':
                        output.update(serialized_record=planning_graph.serialize_ticket_plan(value),
                                      mermaid=planning_graph.render_mermaid(value))
                    # Copy to bytes immediately: later mutations must not alter history.
                    events[current[0]].append(canonical(output).decode('utf-8') + '\n')
                return value
            except (ValueError, TypeError) as exc:
                if outer and current[0]:
                    events[current[0]].append(canonical(dict(
                        function=name, refusal=type(exc).__name__, message=str(exc))).decode('utf-8') + '\n')
                raise
            finally:
                depth[0] -= 1
        return call

    stream = io.StringIO()
    with ExitStack() as stack:
        for module, names in ((planning, ('plan_lanes', 'recompute_plan')),
                              (planning_graph, ('build_ticket_plan', 'refill_rank'))):
            for name in names:
                stack.enter_context(patch.object(module, name, wrapper(
                    module.__name__.split('.')[-1] + '.' + name, getattr(module, name))))
        suite = unittest.defaultTestLoader.loadTestsFromNames(SUITES)
        result = unittest.TextTestRunner(stream=stream, resultclass=Result, verbosity=1).run(suite)
    if not result.wasSuccessful() or result.skipped:
        raise AssertionError('Baseline scenario suite failed or skipped:\n' + stream.getvalue())
    return events, result.testsRun


def freeze():
    """Explicit developer operation, refused unless original inputs match Git."""
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=ROOT)
    if git('rev-parse', 'HEAD').decode().strip() != BASE:
        raise RuntimeError('Freeze requires the recorded origin/main baseline HEAD')
    if git('rev-parse', 'origin/main').decode().strip() != BASE:
        raise RuntimeError('Freeze requires the recorded origin/main baseline ref')
    sources = {}
    for name in SOURCE_PATHS:
        committed = git('show', BASE + ':' + name)
        # Git's checkout filter may use CRLF; compare Git object bytes to the
        # cleaned working file, then preserve output bytes without normalization.
        cleaned = git('hash-object', '--path=' + name, name).decode().strip()
        if cleaned != git('rev-parse', BASE + ':' + name).decode().strip():
            raise RuntimeError('Baseline input changed: ' + name)
        sources[name] = hashlib.sha256(committed).hexdigest()
    events, count = capture()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    if list(FIXTURES.iterdir()):
        raise RuntimeError('Frozen expectations are immutable; use a new baseline directory')
    # Per-test JSON keeps deep-chain variants inspectable and each file bounded.
    index = {}
    for name, outputs in sorted(events.items()):
        filename = name + '.json'
        raw = canonical(outputs) + b'\n'
        (FIXTURES / filename).write_bytes(raw)
        index[name] = dict(file=filename, sha256=hashlib.sha256(raw).hexdigest(), outputs=len(outputs))
    (FIXTURES / 'index.json').write_bytes(canonical(dict(
        format='awf-sprint-off-baseline-1', source_commit=BASE, source_sha256=sources,
        command='python -B .agentic/tests/test_sprint_off_baseline.py --freeze-origin-main',
        suites=list(SUITES), tests_run=count, cases=index)) + b'\n')
    print(f'Frozen {count} tests, {sum(len(v) for v in events.values())} complete outputs')


class OffBaselineTests(unittest.TestCase):
    def test_existing_planning_outputs_match_origin_main_bytes(self):
        index = json.loads((FIXTURES / 'index.json').read_bytes())
        self.assertEqual(BASE, index['source_commit'])
        actual, count = capture()
        self.assertEqual(index['tests_run'], count)
        self.assertEqual(set(index['cases']), set(actual))
        for name, metadata in index['cases'].items():
            with self.subTest(scenario=name):
                expected = (FIXTURES / metadata['file']).read_bytes()
                self.assertEqual(metadata['sha256'], hashlib.sha256(expected).hexdigest())
                self.assertEqual(metadata['outputs'], len(actual[name]))
                self.assertEqual(expected, canonical(actual[name]) + b'\n')


if __name__ == '__main__':
    if sys.argv[1:] == ['--freeze-origin-main']:
        freeze()
    else:
        unittest.main()
