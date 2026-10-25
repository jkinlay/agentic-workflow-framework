"""A manifest-only installed tree must self-test with zero failures and errors.

Source-only tests must skip (via .agentic/tests/source_only.py), never error,
when scripts/, tools/, root documents and historical fixtures are absent.

Full Windows runs may select an approved budget with
AWF_INSTALLED_SELF_TEST_TIMEOUT_SECONDS: ASCII decimal seconds, 3600 through
86400 inclusive (default 3600). Any outer watchdog needs sufficient approved
time for that budget and setup. The installed suite and its assertions stay whole.
"""
from __future__ import annotations
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.child_process import child_env  # noqa: E402
from agentic.installer import install  # noqa: E402
from agentic.runtime_commands import installed_paths  # noqa: E402


MIN_INSTALL_TIMEOUT_SECONDS = 180


def load_self_test_module():
    path = ROOT / ".agentic/scripts/self_test.py"
    spec = importlib.util.spec_from_file_location("awf_installed_self_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_timeout_seconds(observed_seconds):
    """Add 50% measured headroom, with twice the former 90s limit."""
    return max(MIN_INSTALL_TIMEOUT_SECONDS, math.ceil(observed_seconds * 1.5))


INSTALL_TIMEOUT_SECONDS = install_timeout_seconds(96.3)


def installed_self_test_timeout_seconds(environ):
    """Validate the installed-suite override before any expensive setup."""
    name = "AWF_INSTALLED_SELF_TEST_TIMEOUT_SECONDS"
    if name not in environ:
        return 3600
    value = environ[name]
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        try:
            seconds = int(value.lstrip("0") or "0")
        except ValueError:
            pass
        else:
            if 3600 <= seconds <= 86400:
                return seconds
    raise ValueError(f"{name} must be an ASCII decimal integer from 3600 through 86400")


def run_with_timeout(command, *, timeout, evidence_parent=None, **kwargs):
    # Only the installed self-test opts into bounded, durable evidence. Other
    # callers retain subprocess.run semantics and complete output reporting.
    if evidence_parent is not None:
        sys.path.insert(0, str(ROOT / 'scripts'))
        from installed_self_test_capture import run_bounded
        return run_bounded(command, timeout=timeout, evidence_parent=evidence_parent, **kwargs)
    def report_output(stdout, stderr):
        # TimeoutExpired may contain bytes even when text=True was requested.
        # Emit all progress before the temporary installed tree is removed.
        retained = []
        for label, output, stream in (
                ("stdout", stdout, sys.stdout), ("stderr", stderr, sys.stderr)):
            if output is None:
                continue
            if isinstance(output, bytes):
                output = output.decode(kwargs.get("encoding") or "utf-8", errors="replace")
            print(output, file=stream, end="", flush=True)
            retained.append(f"{label}:\n{output}")
        return "\n".join(retained)

    started = time.monotonic()
    try:
        completed = subprocess.run(command, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as failure:
        elapsed = time.monotonic() - started
        progress = report_output(failure.stdout, failure.stderr)
        raise AssertionError(
            f"subprocess timeout: elapsed={elapsed:.3f}s; limit={float(timeout):.3f}s; "
            f"command={command!r}\n{progress}"
        ) from failure
    report_output(completed.stdout, completed.stderr)
    return completed


class InstallSubprocessTimeoutTests(unittest.TestCase):
    def test_installed_self_test_budget_defaults_to_one_hour_and_accepts_a_validated_override(self):
        name = "AWF_INSTALLED_SELF_TEST_TIMEOUT_SECONDS"
        self.assertEqual(3600, installed_self_test_timeout_seconds({}))
        for value, expected in (("3600", 3600), ("21600", 21600), ("86400", 86400),
                                ("0003600", 3600), ("0" * 5000 + "3600", 3600)):
            with self.subTest(value=repr(value)[:80]):
                self.assertEqual(expected, installed_self_test_timeout_seconds({name: value}))

    def test_installed_self_test_budget_rejects_invalid_overrides(self):
        name = "AWF_INSTALLED_SELF_TEST_TIMEOUT_SECONDS"
        invalid = ("0", "-1", "3599", "86401", "1e4", " 7200", "abc", "",
                   "+7200", "7200.0", "7200 ", "7200\n", "\t7200", "7_200",
                   "\u0667\u0662\u0660\u0660", "\uff17\uff12\uff10\uff10",
                   None, 7200, 7200.0, True, b"7200", "9" * 5000)
        for value in invalid:
            with self.subTest(value=repr(value)[:80]):
                with self.assertRaisesRegex(ValueError, name):
                    installed_self_test_timeout_seconds({name: value})

    def test_installed_self_test_budget_is_validated_before_setup(self):
        with mock.patch.dict(os.environ, AWF_INSTALLED_SELF_TEST_TIMEOUT_SECONDS="invalid"), \
                mock.patch(__name__ + ".install") as installer, \
                mock.patch(__name__ + ".run_with_timeout") as runner:
            case = InstalledSelfTestTests(
                "test_manifest_only_install_self_test_has_no_failures_or_errors")
            with self.assertRaisesRegex(ValueError, "AWF_INSTALLED_SELF_TEST_TIMEOUT_SECONDS"):
                case.test_manifest_only_install_self_test_has_no_failures_or_errors()
        installer.assert_not_called()
        runner.assert_not_called()

    def test_installed_self_test_budget_reaches_only_the_suite_invocation(self):
        def completed(command, **kwargs):
            if "--checks-only" in command:
                report = {"status": "PASS", "tests": {"failures": 0, "errors": 0,
                    "skipped": [{"test": module + ".fixture", "reason": "requires committed HEAD"}
                                for module in ("test_continuous_controller", "test_heavy_validation")]}}
                Path(command[command.index("--report") + 1]).write_text(
                    json.dumps(report), encoding="utf-8")
            unborn_probe = command[-3:] == ["rev-parse", "--verify", "HEAD"]
            return subprocess.CompletedProcess(command, 1 if unborn_probe else 0, "", "")

        with mock.patch.dict(os.environ, AWF_INSTALLED_SELF_TEST_TIMEOUT_SECONDS="21600"), \
                mock.patch(__name__ + ".install"), \
                mock.patch(__name__ + ".installed_paths", return_value=(None, Path(sys.executable), None)), \
                mock.patch(__name__ + ".run_with_timeout", side_effect=completed) as runner:
            case = InstalledSelfTestTests(
                "test_manifest_only_install_self_test_has_no_failures_or_errors")
            case.test_manifest_only_install_self_test_has_no_failures_or_errors()
        self.assertEqual([600, 1200, INSTALL_TIMEOUT_SECONDS, INSTALL_TIMEOUT_SECONDS, 21600],
                         [call.kwargs["timeout"] for call in runner.call_args_list])
        suite_call = runner.call_args_list[-1]
        self.assertIn("--checks-only", suite_call.args[0])
        self.assertEqual(ROOT / ".tmp-tests/installed-self-test-evidence",
                         suite_call.kwargs["evidence_parent"])

    def capture_child(self, parent, code, timeout=3, **kwargs):
        # Before the implementation, exercise the original helper without a new
        # keyword: RED must demonstrate missing evidence, never a missing API.
        import inspect
        options = {}
        if 'evidence_parent' in inspect.signature(run_with_timeout).parameters:
            options['evidence_parent'] = parent
        output, errors = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(output), redirect_stderr(errors):
                return run_with_timeout([sys.executable, '-B', '-c', code],
                                        timeout=timeout, capture_output=True,
                                        **options, **kwargs)
        finally:
            self.capture_log = output.getvalue() + errors.getvalue()

    def evidence(self, parent, count=1):
        paths = list(Path(parent).glob('*/capture.json'))
        self.assertEqual(count, len(paths), 'missing durable capture.json evidence')
        for path in paths:
            self.assertLessEqual(path.stat().st_size, 262144)
            excerpt = path.with_name('excerpt.txt').read_bytes()
            self.assertLessEqual(len(excerpt), 16384)
            excerpt.decode('utf-8')
        return json.loads(paths[-1].read_text(encoding='utf-8'))

    @staticmethod
    def event_code(events):
        return ('import sys,json,time,os; events=' + repr(events) +
                '; [print(json.dumps(e),file=sys.stderr,flush=True) for e in events]; ')

    def test_capture_1_timeout_evidence(self):
        events = [
            {'event': 'self_test_phase_complete', 'elapsed_seconds': 1,
             'phase': 'discovery', 'phase_elapsed_seconds': .25},
            {'event': 'self_test_phase_start', 'elapsed_seconds': 2, 'phase': 'test_suite'},
            {'event': 'self_test_case_start', 'elapsed_seconds': 3,
             'phase': 'test_suite', 'current_test': 'example.test'}]
        with tempfile.TemporaryDirectory() as parent:
            start = time.monotonic()
            with self.assertRaisesRegex(AssertionError, 'subprocess timeout:.*limit=') as raised:
                self.capture_child(parent, self.event_code(events) +
                                   "print('known output',flush=True); time.sleep(20)", timeout=.4)
            self.assertIsInstance(raised.exception.__cause__, subprocess.TimeoutExpired)
            self.assertLess(time.monotonic() - start, 5.5)
            value = self.evidence(parent)
            self.assertEqual('timeout', value['outcome'])
            self.assertEqual('complete', value['phases'][0]['status'])
            self.assertEqual(.25, value['phases'][0]['phase_elapsed_seconds'])
            self.assertEqual('incomplete', value['phases'][4]['status'])
            self.assertEqual('example.test', value['latest_progress']['current_test'])
            self.assertTrue(value['cleanup']['readers_stopped'])
            self.assertTrue(value['cleanup']['child_reaped'])
            self.assertIn('self-test evidence allocated:', self.capture_log)
            self.assertEqual(2, self.capture_log.count('self-test evidence saved:'))

    def test_capture_2_successful_timings_and_compatibility(self):
        phases = ('discovery', 'syntax', 'documentation', 'release_hygiene', 'test_suite')
        events = []
        for i, phase in enumerate(phases):
            events += [{'event': 'self_test_phase_start', 'phase': phase, 'elapsed_seconds': i},
                       {'event': 'self_test_phase_complete', 'phase': phase,
                        'elapsed_seconds': i + .5, 'phase_elapsed_seconds': i + .125}]
        events += [events[0], {'event': 'self_test_case_start', 'phase': 'test_suite',
                              'elapsed_seconds': 6, 'current_test': 'old'},
                   {'event': 'self_test_heartbeat', 'phase': None, 'current_test': None,
                    'last_completed_phase': 'test_suite', 'elapsed_seconds': 7}]
        for text in (True, False):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as parent:
                code = self.event_code(events) + "print(json.dumps({'report': True},indent=2)); print('{bad',file=sys.stderr); print('{\"event\":\"unknown\"}',file=sys.stderr)"
                result = self.capture_child(parent, code, text=text)
                self.assertIsInstance(result.stdout, str if text else bytes)
                value = self.evidence(parent)
                self.assertEqual([i + .125 for i in range(5)],
                                 [p['phase_elapsed_seconds'] for p in value['phases']])
                self.assertIsNone(value['latest_progress']['current_test'])
                self.assertEqual(1, value['parser']['duplicates'])
                self.assertGreaterEqual(value['parser']['rejected'], 2)
        with tempfile.TemporaryDirectory() as parent:
            env = dict(os.environ, AWF_CAPTURE_TEST='value')
            result = self.capture_child(parent, "import os,sys; print(os.environ['AWF_CAPTURE_TEST']); print(os.getcwd()); sys.stdout.buffer.write(b'\\xff'); sys.exit(7)",
                                        text=True, encoding='utf-8', errors='replace', env=env, cwd=parent)
            self.assertEqual(7, result.returncode)
            self.assertIn('value', result.stdout)
            self.assertIn(parent, result.stdout)
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                self.capture_child(parent, 'import sys; sys.exit(9)', check=True)
            self.assertEqual(9, raised.exception.returncode)

    def test_capture_launch_environments_strip_provider_keys(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import installed_self_test_capture as capture
        from agentic.child_process import PROVIDER_API_KEY_ENV_VARS

        provider_values = {name.swapcase(): 'dummy-provider-value'
                           for name in PROVIDER_API_KEY_ENV_VARS}
        ambient = dict(provider_values, AWF_CAPTURE_SENTINEL='ambient')
        real_popen = subprocess.Popen
        for mode in ('omitted', 'none', 'mapping'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as parent:
                supplied = dict(os.environ, **provider_values,
                                AWF_CAPTURE_SENTINEL='supplied')
                original = supplied.copy()
                options = {} if mode == 'omitted' else {
                    'env': None if mode == 'none' else supplied}
                with mock.patch.dict(os.environ, ambient), \
                        mock.patch.object(capture.subprocess, 'Popen',
                                          wraps=real_popen) as spawn:
                    result = self.capture_child(
                        parent, "import os; print(os.environ['AWF_CAPTURE_SENTINEL'])",
                        text=True, **options)
                self.assertEqual(0, result.returncode)
                self.assertEqual('supplied\n' if mode == 'mapping' else 'ambient\n',
                                 result.stdout)
                self.assertEqual(original, supplied)
                self.assertEqual(2, spawn.call_count)
                for index, call in enumerate(spawn.call_args_list):
                    environment = call.kwargs.get('env')
                    self.assertIsNotNone(environment, 'launch must supply a scrubbed environment')
                    self.assertFalse(PROVIDER_API_KEY_ENV_VARS &
                                     {name.upper() for name in environment})
                    expected = 'supplied' if index == 1 and mode == 'mapping' else 'ambient'
                    self.assertEqual(expected, environment['AWF_CAPTURE_SENTINEL'])
                value = self.evidence(parent)
                self.assertTrue(value['persistence']['allocation_acknowledged'])
                self.assertEqual(2, self.capture_log.count('self-test evidence saved:'))

    def test_capture_3_cleanup_survival_and_unique_runs(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as parent:
            sentinel = Path(parent) / 'sentinel'
            sentinel.write_text('preserve', encoding='utf-8')
            with tempfile.TemporaryDirectory() as outer:
                self.capture_child(parent, "print('sequential')", cwd=outer)
                with ThreadPoolExecutor(2) as pool:
                    futures = [pool.submit(self.capture_child, parent, "print('parallel')", cwd=outer)
                               for _ in range(2)]
                    for future in futures:
                        future.result(timeout=8)
            self.assertFalse(Path(outer).exists())
            self.evidence(parent, 3)
            self.assertEqual('preserve', sentinel.read_text(encoding='utf-8'))

    def test_capture_4_overflow_and_inherited_handles(self):
        code = ("import os,sys,json; os.write(1,b'x'*180000); os.write(2,b'y'*180000+b'\\n'); "
                "os.write(1,b'\\xe2'); os.write(1,b'\\x82\\xac'); "
                "[print(json.dumps({'event':'self_test_case_start','phase':'test_suite','elapsed_seconds':1,'current_test':'\\x01'*2000}),file=sys.stderr) for _ in range(50)]; "
                "os.write(2,b'['*20+b']'*20+b'\\n'); os.write(2,b'z'*20000)")
        with tempfile.TemporaryDirectory() as parent:
            result = self.capture_child(parent, code)
            value = self.evidence(parent)
            for name in ('stdout', 'stderr'):
                stream = value['streams'][name]
                self.assertLessEqual(stream['retained_raw_bytes'], 65536)
                self.assertEqual(stream['observed_bytes'], stream['retained_raw_bytes'] + stream['discarded_raw_bytes'])
                self.assertTrue(stream['eof'])
            self.assertLessEqual(value['parser']['carry_high_water'], 16384)
            self.assertEqual(5, len(value['phases']))
            self.assertLessEqual(len(value['latest_progress']['current_test'].encode()), 1024)
            self.assertGreater(value['parser']['incomplete_records'], 0)
            self.assertLessEqual(len(result.stdout), 65536)
        with tempfile.TemporaryDirectory() as parent:
            with self.assertRaises(AssertionError) as raised:
                self.capture_child(parent,
                    "import os,time; os.write(1,b'\\x00'*90000); os.write(2,b'\\x01'*90000); time.sleep(20)",
                    timeout=.4)
            prefix, _, tails = str(raised.exception).partition('\n')
            self.assertIn('limit=0.400s', prefix)
            self.assertLessEqual(len(tails.encode('utf-8')), 8192)
            self.assertIn('stdout:\n', tails)
            self.assertIn('stderr:\n', tails)
            self.evidence(parent)
        with tempfile.TemporaryDirectory() as parent:
            start = time.monotonic()
            self.capture_child(parent, "import subprocess,sys; subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(2)']); print('parent exited')")
            self.assertLess(time.monotonic() - start, 2)
            value = self.evidence(parent)
            self.assertFalse(value['streams']['stdout']['eof'])
            # The descendant is finite and owns no files; allow its bounded exit.
            time.sleep(2)

    def test_capture_5_persistence_failures_preserve_timeout(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        try:
            import installed_self_test_capture as capture
        except ModuleNotFoundError:
            capture = None  # Also run the strengthened oracle against baseline.
        original_command = capture.worker_command if capture else None
        for fault in ('allocation', 'open', 'write', 'finalization', 'stall'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as parent:
                start = time.monotonic()
                from contextlib import nullcontext
                injection = (mock.patch.object(capture, 'worker_command',
                             side_effect=lambda p: original_command(p) + [fault])
                             if capture else nullcontext())
                with injection:
                    with self.assertRaisesRegex(AssertionError, 'subprocess timeout:') as raised:
                        self.capture_child(parent, "import time; print('progress',flush=True); time.sleep(20)", timeout=.2)
                self.assertIsInstance(raised.exception.__cause__, subprocess.TimeoutExpired)
                self.assertLess(time.monotonic() - start, 5)
                self.assertFalse(list(Path(parent).glob('*/capture.json')))
                self.assertIn('self-test evidence diagnostic:', self.capture_log)
                self.assertNotIn('self-test evidence saved:', self.capture_log)

    def test_capture_2_input_decoding_and_rejected_options(self):
        with tempfile.TemporaryDirectory() as parent:
            result = self.capture_child(parent,
                "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data+b'\\r\\nend\\r')",
                input='caf\u00e9', encoding='latin-1', universal_newlines=True)
            self.assertEqual('caf\u00e9\nend\n', result.stdout)
            empty = self.capture_child(parent, "import sys; print(len(sys.stdin.buffer.read()))",
                                       stdin=subprocess.PIPE, text=True)
            self.assertEqual('0\n', empty.stdout)
            binary = self.capture_child(parent, 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())',
                                        input=b'\x00\xff')
            self.assertEqual(b'\x00\xff', binary.stdout)
            unicode_result = self.capture_child(parent,
                "import os; os.write(1,('\\u20ac'*22000).encode('utf-8'))",
                text=True, encoding='utf-8')
            self.assertTrue(unicode_result.stdout)
            self.assertEqual({'\u20ac'}, set(unicode_result.stdout))
            self.assertLessEqual(len(unicode_result.stdout.encode('utf-8')), 65536)
            with self.assertRaises(UnicodeDecodeError):
                self.capture_child(parent, "import sys; sys.stdout.buffer.write(b'\\xff')",
                                   text=True, encoding='utf-8')
            for options in ({'capture_output': False}, {'stdout': subprocess.DEVNULL},
                            {'unsupported': True}, {'text': True, 'universal_newlines': False},
                            {'capture_output': True, 'encoding': 'utf-7'}):
                with self.subTest(options=options):
                    sys.path.insert(0, str(ROOT / 'scripts'))
                    from installed_self_test_capture import run_bounded
                    with mock.patch('subprocess.Popen') as spawn:
                        with self.assertRaises(ValueError):
                            run_bounded(['unused'], timeout=1, evidence_parent=parent, **options)
                        spawn.assert_not_called()

    def test_capture_4_parser_boundaries_and_skeleton(self):
        sys.path.insert(0, str(ROOT / 'scripts'))
        import installed_self_test_capture as capture
        parser = capture.Parser()
        for raw in (b'{"event":"self_test_start","event":"self_test_start"}\n',
                    b'{"event":"self_test_phase_start","phase":"syntax","elapsed_seconds":true}\n',
                    b'{"event":"self_test_phase_start","phase":"syntax","elapsed_seconds":1e309}\n',
                    b'[' * 9 + b']' * 9 + b'\n',
                    b'x' * 16384 + b'\n', b'x' * 16385 + b'\n'):
            for offset in range(0, len(raw), 17):
                parser.feed(raw[offset:offset + 17])
        self.assertEqual(6, parser.counts['rejected'])
        self.assertEqual(16384, parser.counts['carry_high_water'])
        self.assertEqual(1, parser.reasons['oversized'])
        parser.feed(b'{"event":"self_test_case_start","phase":"test_suite","elapsed_seconds":1,"current_test":"\\ud800"}\n')
        self.assertEqual(7, parser.counts['rejected'])
        before = parser.counts['rejected']
        parser.feed(b'{"event":"self_test_phase_start","phase":"syntax","elapsed_seconds":' + b'9'*1000 + b'}\n')
        self.assertEqual(before + 1, parser.counts['rejected'])
        for i in range(100):
            parser.feed((json.dumps({'event': 'self_test_case_start', 'phase': 'test_suite',
                                     'elapsed_seconds': i, 'current_test': '\u20ac' * 1000,
                                     'ignored': 'do not retain'}) + '\n').encode())
        self.assertEqual(100, parser.counts['ignored_unknown_fields'])
        self.assertLessEqual(len(parser.latest['current_test'].encode()), 1024)
        self.assertNotIn('ignored', parser.latest)
        self.assertEqual(5, len(parser.phases))
        counts = capture.Counts(value=capture.MAX_COUNT)
        counts.add('value')
        self.assertTrue(counts['saturated'])
        self.assertEqual(capture.MAX_COUNT, counts['value'])
        with tempfile.TemporaryDirectory() as parent:
            # NUL expands sixfold in JSON: this forces real serialization shrink.
            self.capture_child(parent, "import os; os.write(1,b'\\x00'*100000); os.write(2,b'\\x01'*100000)")
            value = self.evidence(parent)
            self.assertGreater(value['serialization']['initial_encoded_bytes'], 262144)
            self.assertEqual(['tails'], value['serialization']['reductions'])
            self.assertEqual(value['serialization']['final_encoded_bytes'],
                             len(list(Path(parent).glob('*/capture.json'))[0].read_bytes()))
            for name in ('stdout', 'stderr'):
                accounting = value['serialization'][name]
                self.assertEqual(65536, accounting['represented_raw_bytes'] + accounting['omitted_raw_bytes'])
            # Max-width counters and all optional fields populate the skeleton.
            value['latest_progress'] = {'event': 'self_test_interrupted', 'elapsed_seconds': 10**12,
                                        'phase': 'test_suite', 'last_completed_phase': 'release_hygiene',
                                        'current_test': 'x' * 1024, 'interruption': 'x' * 256, 'detail': 'x' * 256}
            value['diagnostics'] = {'entries': ['x' * 256] * 8, 'omitted': capture.MAX_COUNT, 'saturated': True}
            def widen(obj):
                if isinstance(obj, dict):
                    for key, item in obj.items():
                        if type(item) is int:
                            obj[key] = capture.MAX_COUNT
                        else:
                            widen(item)
                elif isinstance(obj, list):
                    for item in obj:
                        widen(item)
            widen(value['parser'])
            widen(value['streams'])
            encoded = capture.serialize(value)
            self.assertLessEqual(len(encoded), 262144)
            self.assertLessEqual(value['serialization']['metadata_skeleton_bytes'], 16384)
            for size in (0, 1, 10, 11, 1024):
                self.assertLessEqual(len(capture.bounded('\u20ac' * 2000, size).encode()), size)


    def test_timeout_retains_child_progress_after_temporary_directory_cleanup(self):
        for as_bytes in (False, True):
            with self.subTest(as_bytes=as_bytes):
                stdout, stderr = self.child_progress()
                output = stdout.encode("utf-8") if as_bytes else stdout
                errors = stderr.encode("utf-8") if as_bytes else stderr
                failure = subprocess.TimeoutExpired(
                    ["synthetic-installed-self-test"], 3600,
                    output=output, stderr=errors)
                retained_out, retained_err = io.StringIO(), io.StringIO()
                with tempfile.TemporaryDirectory() as raw:
                    with redirect_stdout(retained_out), redirect_stderr(retained_err), \
                            mock.patch("subprocess.run", side_effect=failure) as run, \
                            mock.patch("time.monotonic", side_effect=[25.0, 3625.5]):
                        with self.assertRaises(AssertionError) as raised:
                            run_with_timeout(
                                failure.cmd, timeout=3600, capture_output=True,
                                text=True, cwd=Path(raw))
                self.assertFalse(Path(raw).exists())
                self.assertEqual(stdout, retained_out.getvalue())
                self.assertEqual(stderr, retained_err.getvalue())
                self.assertIn("elapsed=3600.500s; limit=3600.000s", str(raised.exception))
                self.assertIn(repr(failure.cmd), str(raised.exception))
                self.assertIn(stdout, str(raised.exception))
                self.assertIn(stderr, str(raised.exception))
                self.assertIs(failure, raised.exception.__cause__)
                self.assertEqual(3600, run.call_args.kwargs["timeout"])

    def test_success_reports_child_progress_after_temporary_directory_cleanup(self):
        for as_bytes in (False, True):
            with self.subTest(as_bytes=as_bytes):
                stdout, stderr = self.child_progress()
                completed = subprocess.CompletedProcess(
                    ["synthetic-installed-self-test"], 0,
                    stdout=stdout.encode("utf-8") if as_bytes else stdout,
                    stderr=stderr.encode("utf-8") if as_bytes else stderr)
                retained_out, retained_err = io.StringIO(), io.StringIO()
                with tempfile.TemporaryDirectory() as raw:
                    with redirect_stdout(retained_out), redirect_stderr(retained_err), \
                            mock.patch("subprocess.run", return_value=completed) as run:
                        result = run_with_timeout(
                            completed.args, timeout=3600, capture_output=True,
                            text=not as_bytes, cwd=Path(raw))
                self.assertFalse(Path(raw).exists())
                self.assertEqual(stdout, retained_out.getvalue())
                self.assertEqual(stderr, retained_err.getvalue())
                self.assertIs(completed, result)
                self.assertEqual(3600, run.call_args.kwargs["timeout"])

    @staticmethod
    def child_progress():
        stdout = json.dumps({"phase_timings_seconds": {
            "discovery": 8.183, "syntax": 2.666, "documentation": 0.037,
            "release_hygiene": 0.0}}) + "\n"
        stderr = "\n".join(json.dumps(event) for event in (
            {"event": "self_test_phase_complete", "phase": "discovery",
             "phase_elapsed_seconds": 8.183},
            {"event": "self_test_phase_start", "phase": "test_suite",
             "last_completed_phase": "release_hygiene"},
            {"event": "self_test_case_start", "phase": "test_suite",
             "current_test": "test_example.ExampleTests.test_portable_fixture"},
        )) + "\n"
        return stdout, stderr

    def test_ticket_plan_is_classified_as_a_non_form_schema(self):
        module = load_self_test_module()
        schemas = {
            "candidate", "project-config", "evidence-bundle", "operating-config",
            "ticket-plan", "worker-result",
        }
        self.assertEqual(
            {"worker-result"},
            module.unfilled_form_schema_names(schemas, {"operating-config"}),
        )

    def test_timeout_limit_has_documented_margin_over_observed_controller_load(self):
        self.assertEqual(180, install_timeout_seconds(96.3))
        self.assertEqual(300, install_timeout_seconds(200.0))

    def test_timeout_failure_reports_elapsed_time_and_limit(self):
        failure = subprocess.TimeoutExpired(["synthetic-installed-self-test"], 600)
        with mock.patch("subprocess.run", side_effect=failure), \
                mock.patch("time.monotonic", side_effect=[25.0, 625.5]):
            with self.assertRaisesRegex(
                    AssertionError, r"elapsed=600\.500s; limit=600\.000s"):
                run_with_timeout(["synthetic-installed-self-test"], timeout=600)


def git(cwd, *args):
    run_with_timeout(
        ["git", "-c", "user.name=AWF Test", "-c", "user.email=awf@example.invalid",
         "-C", str(cwd), *args], check=True, capture_output=True,
        env=child_env(dict(os.environ)), timeout=INSTALL_TIMEOUT_SECONDS)


@unittest.skipUnless((ROOT / "MANIFEST.json").is_file(), "source repository only")
class InstalledSelfTestTests(unittest.TestCase):
    def assert_unborn_head(self, project):
        head = run_with_timeout(
            ["git", "-C", str(project), "rev-parse", "--verify", "HEAD"],
            capture_output=True, text=True, check=False, env=child_env(dict(os.environ)),
            timeout=INSTALL_TIMEOUT_SECONDS)
        self.assertNotEqual(0, head.returncode, "fixture unexpectedly has a commit")

    def test_manifest_only_install_self_test_has_no_failures_or_errors(self):
        suite_timeout = installed_self_test_timeout_seconds(os.environ)
        pin = hashlib.sha256((ROOT / "MANIFEST.json").read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="awf-installed-self-test-") as raw:
            project = Path(raw) / "project"
            project.mkdir()
            install(ROOT, project, pin, mode="install", discover=False)
            self.assertFalse((project / "scripts").exists())
            self.assertFalse((project / "MANIFEST.json").exists())
            # An adopter may own a root MANIFEST.json; it must not make the
            # installed project look like the AWF source repository.
            (project / "MANIFEST.json").write_text('{"adopter": "owned"}\n', encoding="utf-8")
            # AWF-39: a fresh installed project may not yet have owner root
            # attributes. Its diagnostics tests must be host-independent.
            self.assertFalse((project / ".gitattributes").exists())
            # Managed runtime: .agentic/.venv with the hash-locked dependencies.
            _, interpreter, _ = installed_paths(project)
            base = getattr(sys, "_base_executable", None) or sys.executable
            env = child_env(dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            # POSIX venvs symlink python by default, which the heavy-validation
            # controller refuses as an alias; Windows venvs always copy.
            copies = [] if os.name == "nt" else ["--copies"]
            run_with_timeout(
                [base, "-m", "venv", *copies, str(project / ".agentic/.venv")], check=True,
                capture_output=True, env=env, timeout=600)
            run_with_timeout(
                [str(interpreter), "-m", "pip", "install", "--disable-pip-version-check",
                 "--require-hashes", "--only-binary=:all:", "-r",
                 str(project / ".agentic/requirements.lock")], check=True,
                capture_output=True, env=env, timeout=1200)
            git(project, "init", "-q", "-b", "main")
            report_path = Path(raw) / "self-test.json"
            # Keep this immediately before self-test: the installed-runtime
            # acceptance case is specifically an initialized, unborn repository.
            self.assert_unborn_head(project)
            done = run_with_timeout(
                [str(interpreter), "-B", str(project / ".agentic/scripts/self_test.py"),
                 "--checks-only", "--report", str(report_path)], cwd=project,
                capture_output=True, text=True, timeout=suite_timeout,
                evidence_parent=ROOT / '.tmp-tests/installed-self-test-evidence',
                env=child_env(dict(os.environ, PYTHONDONTWRITEBYTECODE="1")))
            report = json.loads(report_path.read_text(encoding="utf-8"))
            failed = [line for line in str(report.get("test_log", "")).splitlines()
                      if line.startswith(("FAIL:", "ERROR:"))]
            tests = report.get("tests", {})
            self.assertEqual((0, 0), (tests.get("failures"), tests.get("errors")), failed or done.stderr[-3000:])
            self.assertEqual("PASS", report["status"], report.get("error"))
            self.assertEqual(0, done.returncode)
            skipped = tests.get("skipped", [])
            for item in skipped:
                self.assertTrue(item.get("test"), item)
                self.assertTrue(item.get("reason"), item)
            for module in ("test_continuous_controller", "test_heavy_validation"):
                matching = [item for item in skipped if module in item["test"]]
                self.assertEqual(1, len(matching), skipped)
                self.assertIn("committed HEAD", matching[0]["reason"])

    def test_unborn_head_guard_rejects_existing_head(self):
        completed = subprocess.CompletedProcess(
            ["git", "rev-parse", "--verify", "HEAD"], 0, stdout="a" * 40 + "\n", stderr="")
        with mock.patch("subprocess.run", return_value=completed):
            with self.assertRaisesRegex(AssertionError, "unexpectedly has a commit"):
                self.assert_unborn_head(Path("installed-project"))


if __name__ == "__main__":
    unittest.main()
