"""Process-boundary regressions for the shared bounded runner."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401

from agentic import ValidationError
from agentic import safeio


def _python(source, *arguments):
    return [sys.executable, "-B", "-c", source, *map(str, arguments)]


class BoundedProcessTests(unittest.TestCase):
    def test_opt_in_streaming_does_not_retain_stdout(self):
        received = []
        result = safeio.run_bounded(
            _python("import sys; sys.stdout.buffer.write(b'x' * 131072)"),
            retain_stdout=False,
            stream_sink=lambda channel, chunk: received.append((channel, chunk)),
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual(b"", result.stdout)
        self.assertEqual(b"x" * 131072, b"".join(
            chunk for channel, chunk in received if channel == "stdout"))

    def test_nonretaining_streaming_still_counts_all_output_bytes(self):
        with self.assertRaisesRegex(ValidationError, "SUBPROCESS_STDOUT_LIMIT"):
            safeio.run_bounded(
                _python("import sys; sys.stdout.buffer.write(b'x' * 131072)"),
                stdout_limit=65536, stderr_limit=65536,
                aggregate_output_limit=131072, retain_stdout=False,
                stream_sink=lambda _channel, _chunk: None,
            )

    def test_environment_snapshot_is_validated_immutable_and_reused(self):
        from agentic import child_process
        supplied = dict(os.environ, OPENAI_API_KEY="fixture-provider", CUSTOM_SECRET="fixture-extra")
        with mock.patch.object(child_process, "configured_child_env_strip_extra",
                               return_value=frozenset({"CUSTOM_SECRET"})) as loader:
            snapshot = safeio.ChildEnvironmentSnapshot.capture(supplied)
            for _ in range(2):
                result = safeio.run_bounded(
                    _python("import os; print('OPENAI_API_KEY' in os.environ); "
                            "print('CUSTOM_SECRET' in os.environ)"),
                    environment_snapshot=snapshot,
                )
                self.assertEqual(["False", "False"], result.stdout.decode().splitlines())
        self.assertEqual(1, loader.call_count)
        self.assertEqual("fixture-provider", supplied["OPENAI_API_KEY"])
        self.assertEqual("fixture-extra", supplied["CUSTOM_SECRET"])
        with self.assertRaises(TypeError):
            snapshot.environment["CUSTOM_SECRET"] = "restored"

    def test_streaming_options_and_snapshot_reject_unsafe_combinations_before_launch(self):
        snapshot = safeio.ChildEnvironmentSnapshot.capture()
        cases = (
            {"retain_stdout": False},
            {"retain_stdout": 0, "stream_sink": lambda *_: None},
            {"environment_snapshot": {}},
            {"environment_snapshot": snapshot, "env": dict(os.environ)},
        )
        for options in cases:
            with self.subTest(options=list(options)), \
                    mock.patch.object(safeio.subprocess, "Popen") as launch, \
                    self.assertRaises(ValidationError):
                safeio.run_bounded(_python("pass"), **options)
            launch.assert_not_called()

    def test_snapshot_capture_fails_closed_on_invalid_configuration(self):
        from agentic import child_process
        with mock.patch.object(child_process, "configured_child_env_strip_extra",
                               side_effect=child_process.ChildEnvironmentError("invalid policy")):
            with self.assertRaises(child_process.ChildEnvironmentError):
                safeio.ChildEnvironmentSnapshot.capture()

    def test_snapshot_capture_copies_values_and_resolves_fresh_policy(self):
        from agentic import child_process
        supplied = dict(os.environ, CUSTOM_SECRET="fixture", SNAPSHOT_VALUE="before")
        with mock.patch.object(child_process, "configured_child_env_strip_extra",
                               side_effect=(frozenset({"CUSTOM_SECRET"}), frozenset())) as loader:
            first = safeio.ChildEnvironmentSnapshot.capture(supplied)
            supplied["SNAPSHOT_VALUE"] = "after"
            second = safeio.ChildEnvironmentSnapshot.capture(supplied)
        self.assertEqual(2, loader.call_count)
        self.assertNotIn("CUSTOM_SECRET", first.environment)
        self.assertIn("CUSTOM_SECRET", second.environment)
        self.assertEqual("before", first.environment["SNAPSHOT_VALUE"])
        self.assertEqual("after", second.environment["SNAPSHOT_VALUE"])
        with self.assertRaises(AttributeError):
            first.environment = {}

    def test_nonretaining_stdout_keeps_aggregate_and_shared_operation_accounting(self):
        from agentic.child_process import OperationBudget
        source = "import sys; sys.stdout.buffer.write(b'o' * 6); sys.stderr.buffer.write(b'e' * 6)"
        with self.assertRaisesRegex(ValidationError, "SUBPROCESS_AGGREGATE_LIMIT"):
            safeio.run_bounded(_python(source), stdout_limit=8, stderr_limit=8,
                               aggregate_output_limit=10, retain_stdout=False,
                               stream_sink=lambda *_: None)
        budget = OperationBudget("fixture", byte_limit=10)
        with self.assertRaisesRegex(ValidationError, "SUBPROCESS_OPERATION_BUDGET"):
            safeio.run_bounded(_python(source), operation_budget=budget,
                               retain_stdout=False, stream_sink=lambda *_: None)
        self.assertEqual(1, budget.operations)
        self.assertGreater(budget.consumed_bytes, 0)

    def test_nonretaining_streaming_keeps_closed_policy_caps(self):
        from agentic.child_process import GIT_RUNNER_POLICY
        with mock.patch.object(safeio.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(ValidationError, "SUBPROCESS_POLICY"):
                safeio.run_bounded(_python("pass"), policy=GIT_RUNNER_POLICY,
                                   stdout_limit=GIT_RUNNER_POLICY.stdout_limit + 1,
                                   retain_stdout=False, stream_sink=lambda *_: None)
        launch.assert_not_called()

    def test_nonretaining_sink_failure_reaps_owned_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "streaming-grandchild-finished"
            grandchild = (
                "import pathlib,sys,time; time.sleep(.45); "
                "pathlib.Path(sys.argv[1]).write_text('escaped', encoding='utf-8')"
            )
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]]); "
                "print('ready', flush=True); time.sleep(10)"
            )

            def refuse(_channel, _chunk):
                raise ValueError("fixture refusal")

            with self.assertRaisesRegex(ValidationError, "SUBPROCESS_STREAM_SINK"):
                safeio.run_bounded(_python(parent, grandchild, marker),
                                   retain_stdout=False, stream_sink=refuse)
            time.sleep(0.7)
            self.assertFalse(marker.exists())

    def test_job_setup_failure_closes_already_pinned_watched_outputs(self):
        class RecordingWatcher:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        watcher = RecordingWatcher()
        with mock.patch.object(safeio, "_PinnedWatchedOutput", return_value=watcher), \
                mock.patch.object(safeio.os, "name", "nt"), \
                mock.patch.object(safeio, "_WindowsJob", side_effect=OSError("job setup")):
            with self.assertRaisesRegex(OSError, "job setup"):
                safeio.run_bounded(_python("pass"), watched_files=["watched"])
        self.assertTrue(watcher.closed)

    def test_thread_start_failure_reaps_child_and_closes_owned_resources(self):
        class RecordingStream:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class RecordingProcess:
            def __init__(self):
                self.pid = 12345
                self.stdout = RecordingStream()
                self.stderr = RecordingStream()
                self.stdin = None
                self.returncode = None
                self.killed = False

            def poll(self):
                return self.returncode

            def kill(self):
                self.killed = True
                self.returncode = -1

            def wait(self):
                if self.returncode is None:
                    self.returncode = 0
                return self.returncode

        class RecordingJob:
            creationflags = 0

            def attach_and_resume(self, _process):
                pass

            def close(self):
                pass

        class FailingThread:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                raise RuntimeError("thread start")

            def join(self, timeout=None):
                pass

        class RecordingWatcher:
            snapshot = None

            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        process = RecordingProcess()
        watcher = RecordingWatcher()
        with mock.patch.object(safeio, "_PinnedWatchedOutput", return_value=watcher), \
                mock.patch.object(safeio, "_WindowsJob", return_value=RecordingJob()), \
                mock.patch.object(safeio.subprocess, "Popen", return_value=process), \
                mock.patch.object(safeio.threading, "Thread", FailingThread), \
                mock.patch.object(
                    safeio, "_terminate_owned_tree",
                    side_effect=lambda owned, _job: owned.kill(),
                ):
            with self.assertRaisesRegex(RuntimeError, "thread start"):
                safeio.run_bounded(_python("pass"), watched_files=["watched"])
        self.assertTrue(process.killed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertTrue(watcher.closed)

    def test_stdin_has_an_independent_bound(self):
        with self.assertRaisesRegex(ValidationError, "SUBPROCESS_STDIN_LIMIT"):
            safeio.run_bounded(
                _python("import sys; sys.stdin.buffer.read()"),
                stdin=b"12345", stdin_limit=4,
            )

    def test_stdout_and_stderr_share_one_aggregate_bound(self):
        command = _python(
            "import sys; "
            "sys.stdout.buffer.write(b'o' * 6); sys.stdout.buffer.flush(); "
            "sys.stderr.buffer.write(b'e' * 6); sys.stderr.buffer.flush()"
        )
        with self.assertRaisesRegex(ValidationError, "SUBPROCESS_AGGREGATE_LIMIT"):
            safeio.run_bounded(
                command, stdout_limit=8, stderr_limit=8,
                aggregate_output_limit=10,
            )

    def test_stdout_and_stderr_each_have_an_independent_bound(self):
        cases = (
            ("import sys; sys.stdout.buffer.write(b'x' * 5)", "SUBPROCESS_STDOUT_LIMIT"),
            ("import sys; sys.stderr.buffer.write(b'x' * 5)", "SUBPROCESS_STDERR_LIMIT"),
        )
        for source, reason in cases:
            received = []
            with self.subTest(reason=reason), self.assertRaisesRegex(ValidationError, reason):
                safeio.run_bounded(
                    _python(source), stdout_limit=4, stderr_limit=4,
                    aggregate_output_limit=8,
                    stream_sink=lambda channel, chunk: received.append((channel, chunk)),
                )
            self.assertEqual(received, [])

    def test_cancellation_terminates_an_owned_child(self):
        cancelled = threading.Event()
        timer = threading.Timer(0.1, cancelled.set)
        timer.start()
        try:
            with self.assertRaisesRegex(ValidationError, "SUBPROCESS_CANCELLED"):
                safeio.run_bounded(
                    _python("import time; time.sleep(10)"),
                    cancel_event=cancelled, timeout=5,
                )
        finally:
            timer.cancel()

    def test_timeout_reaps_the_owned_process_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "grandchild-finished"
            grandchild = (
                "import pathlib,sys,time; time.sleep(.45); "
                "pathlib.Path(sys.argv[1]).write_text('escaped', encoding='utf-8')"
            )
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]]); "
                "time.sleep(10)"
            )
            with self.assertRaisesRegex(ValidationError, "SUBPROCESS_TIMEOUT"):
                safeio.run_bounded(
                    _python(parent, grandchild, marker), timeout=0.1,
                )
            time.sleep(0.7)
            self.assertFalse(marker.exists(), "a grandchild escaped the owned process tree")

    def test_cancellation_reaps_the_owned_process_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "cancelled-grandchild-finished"
            grandchild = (
                "import pathlib,sys,time; time.sleep(.45); "
                "pathlib.Path(sys.argv[1]).write_text('escaped', encoding='utf-8')"
            )
            parent = (
                "import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-B','-c',sys.argv[1],sys.argv[2]]); "
                "time.sleep(10)"
            )
            cancelled = threading.Event()
            timer = threading.Timer(0.1, cancelled.set)
            timer.start()
            try:
                with self.assertRaisesRegex(ValidationError, "SUBPROCESS_CANCELLED"):
                    safeio.run_bounded(
                        _python(parent, grandchild, marker),
                        cancel_event=cancelled, timeout=5,
                    )
            finally:
                timer.cancel()
            time.sleep(0.7)
            self.assertFalse(marker.exists(), "a cancelled grandchild escaped its owned tree")

    def test_streaming_sink_receives_bounded_channel_chunks(self):
        received = []
        result = safeio.run_bounded(
            _python("import sys; print('out'); print('err', file=sys.stderr)"),
            stream_sink=lambda channel, chunk: received.append((channel, chunk)),
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(b"".join(chunk for channel, chunk in received if channel == "stdout"),
                         ("out" + os.linesep).encode())
        self.assertEqual(b"".join(chunk for channel, chunk in received if channel == "stderr"),
                         ("err" + os.linesep).encode())

    def test_streaming_sink_failure_reaps_the_child(self):
        def refuse(_channel, _chunk):
            raise OverflowError("fixture sink full")

        with self.assertRaisesRegex(ValidationError, "SUBPROCESS_STREAM_SINK"):
            safeio.run_bounded(
                _python("import sys,time; print('first', flush=True); time.sleep(10)"),
                stream_sink=refuse, timeout=5,
            )

    def test_streaming_output_cannot_bypass_channel_caps(self):
        received = []
        with self.assertRaisesRegex(ValidationError, "SUBPROCESS_STDOUT_LIMIT"):
            safeio.run_bounded(
                _python("import sys; sys.stdout.buffer.write(b'x' * 5)"),
                stdout_limit=4, stderr_limit=4, aggregate_output_limit=8,
                stream_sink=lambda channel, chunk: received.append((channel, chunk)),
            )
        self.assertEqual(received, [])

    def test_watched_output_rejects_a_hardlinked_leaf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original"
            watched = root / "watched"
            original.write_bytes(b"safe")
            os.link(original, watched)
            with self.assertRaisesRegex(ValidationError, "SUBPROCESS_WATCH"):
                safeio.run_bounded(
                    _python("pass"), watched_files=[watched], watched_file_limit=1024,
                )

    def test_watched_output_has_a_live_bound_and_digest_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            watched = Path(directory) / "watched"
            source = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'x' * 5)"
            with self.assertRaisesRegex(ValidationError, "SUBPROCESS_RESULT_FILE_LIMIT"):
                safeio.run_bounded(
                    _python(source, watched),
                    watched_files=[watched], watched_file_limit=4,
                )
            watched.unlink()
            result = safeio.run_bounded(
                _python(source, watched),
                watched_files=[watched], watched_file_limit=8,
            )
            self.assertEqual(result.watched_outputs[0].data, b"x" * 5)
            self.assertEqual(len(result.watched_outputs[0].sha256), 64)

    def test_child_does_not_inherit_an_unlisted_handle(self):
        real_popen = subprocess.Popen
        observed = {}

        def recording_popen(*args, **kwargs):
            observed.update(kwargs)
            return real_popen(*args, **kwargs)

        read_fd, write_fd = os.pipe()
        try:
            os.set_inheritable(write_fd, True)
            source = (
                "import os,sys; fd=int(sys.argv[1]); "
                "\ntry: os.fstat(fd)\nexcept OSError: sys.exit(0)\nsys.exit(9)"
            )
            with mock.patch.object(safeio.subprocess, "Popen", side_effect=recording_popen):
                result = safeio.run_bounded(_python(source, write_fd))
            self.assertEqual(result.returncode, 0)
            self.assertIs(observed.get("close_fds"), True)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    @unittest.skipIf(os.name == "nt", "POSIX backend assertion")
    def test_posix_launch_owns_a_new_session_and_closes_descriptors(self):
        real_popen = subprocess.Popen
        observed = {}

        def recording_popen(*args, **kwargs):
            observed.update(kwargs)
            return real_popen(*args, **kwargs)

        with mock.patch.object(safeio.subprocess, "Popen", side_effect=recording_popen):
            safeio.run_bounded(_python("pass"))
        self.assertIs(observed.get("start_new_session"), True)
        self.assertIs(observed.get("close_fds"), True)

    @unittest.skipUnless(os.name == "nt", "Windows backend assertion")
    def test_windows_launch_attaches_an_owned_job_at_creation(self):
        real_job = safeio._WindowsJob
        events = []

        class RecordingJob(real_job):
            @property
            def creationflags(self):
                result = super().creationflags
                events.append(("suspended", result))
                return result

            def attach_and_resume(self, process):
                events.append(("attach-and-resume", process.pid))
                return super().attach_and_resume(process)

            def close(self):
                events.append(("close", None))
                return super().close()

        with mock.patch.object(safeio, "_WindowsJob", RecordingJob):
            result = safeio.run_bounded(_python("pass"))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(events[0], ("suspended", safeio._CREATE_SUSPENDED))
        self.assertEqual(events[1][0], "attach-and-resume")
        self.assertEqual(events[-1][0], "close")


class CodexRetentionTests(unittest.TestCase):
    """C1 matrix: production host boundary plus direct encoded retention caps."""

    THREAD = "3bca9a10-44c0-4fa0-a069-685d12f5eff3"
    CONTROLLER = "6dcb0314-9954-40c8-84fc-0ed9b9365b69"
    CREDENTIALS = (
        "Authorization: Bearer synthetic-header", "Authorization: custom synthetic-header",
        "password=synthetic-assignment", "--api-key synthetic-cli",
        "https://fixture:synthetic-password@example.invalid/a",
        "github_pat_SYNTHETIC123", "ghp_SYNTHETIC123", "sk-SYNTHETIC123",
        "Bearer synthetic-bare", "token synthetic-bare", "opaque-fixture-value",
    )

    def setUp(self):
        from agentic.providers import github_review_host as host
        self.host = host
        temporary = tempfile.TemporaryDirectory(prefix="awf-c1-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / "state"
        self.run = self.state / "runs" / self.CONTROLLER
        self.run.mkdir(parents=True)
        self.log = self.run / "codex.jsonl"
        self.companion = self.run / "codex.jsonl.stderr"
        config = self.root / "host.json"
        config.write_bytes(b"{}\n")
        executable = Path(sys.executable).resolve()
        self.driver = host.HostDriver({
            "worker_checkout": str(self.root), "critic_checkout": str(self.root),
            "state_dir": str(self.state), "repository": "fixture/project",
            "executables": {"codex": {"path": str(executable),
                "sha256": hashlib.sha256(executable.read_bytes()).hexdigest()}},
            "_config_path": str(config), "config_hash": hashlib.sha256(config.read_bytes()).hexdigest(),
            "command_timeout_seconds": 30, "agent_timeout_seconds": 30,
            "models": {"worker": "fixture", "critic": "fixture"}, "allowed_paths": [],
        }, self.root)
        self.driver._qualification_probe = True
        self.environment = {"GH_TOKEN": "opaque-fixture-value", "OPENAI_API_KEY": "provider-fixture-value",
                            "GIT_PRIVATE_TOKEN": "git-fixture-value"}

    def machine(self, text="ordinary"):
        return (json.dumps({"type": "thread.started", "thread_id": self.THREAD}) + "\n"
                + json.dumps({"type": "item.completed", "text": text}, ensure_ascii=False)
                + "\n").encode("utf-8")

    def invoke(self, stdout=None, stderr=b"", *, returncode=0, fixture=False):
        result = subprocess.CompletedProcess([], returncode, self.machine() if stdout is None else stdout, stderr)
        with mock.patch.object(self.host.os, "environ", self.environment), \
                mock.patch.object(self.host, "enforce_pinned_awf_skill"), \
                mock.patch.object(self.host, "run_bounded", return_value=result) as bounded:
            if fixture:
                with mock.patch.object(self.host.subprocess, "run", return_value=result):
                    value = self.driver.run("codex", ["exec"], log=self.log)
                bounded.assert_not_called()
                return value
            return self.driver.run("codex", ["exec"], log=self.log)

    def retain(self, stdout, stderr=b"", *, maximum=None):
        operation = getattr(safeio, "retain_codex_log", None)
        self.assertTrue(callable(operation), "C1 requires shared preflight before confined retention")
        with safeio.Tree(self.run) as tree:
            return operation(tree, self.log.name, stdout, stderr,
                             maximum=self.host.CODEX_LOG_LIMIT if maximum is None else maximum,
                             credentials=tuple(self.environment.values()), paths=(self.root,))

    def assert_no_retention(self):
        self.assertEqual(list(self.run.iterdir()), [])

    def test_c1_01_machine_credentials_refused_before_retention(self):
        for credential in self.CREDENTIALS + ("provider-fixture-value", "git-fixture-value"):
            with self.subTest(credential=credential):
                with self.assertRaisesRegex(ValidationError, "^CODEX_LOG_CREDENTIAL$"):
                    self.invoke(self.machine(credential))
                self.assert_no_retention()

    def test_c1_02_decoded_nested_values_keys_and_duplicates_cannot_hide_credentials(self):
        payloads = (
            b'{"nested":[{"text":"\\u0073k-SYNTHETIC123"}]}\n',
            b'{"\\u0067hp_SYNTHETIC123":"ordinary"}\n',
            b'{"nested":{"password":"synthetic-split"}}\n',
            b'{"pass\\u0077ord":"synthetic-key"}\n',
            b'{"text":"\\u006fpaque-fixture-value"}\n',
        )
        for raw in payloads:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValidationError, "^CODEX_LOG_CREDENTIAL$"):
                    self.invoke(raw)
                self.assert_no_retention()
        with self.assertRaisesRegex(ValidationError, "^CODEX_LOG_INVALID$"):
            self.invoke(b'{"text":"ghp_SYNTHETIC123","text":"ordinary"}\n')
        self.assert_no_retention()

    def test_c1_03_actual_stderr_credentials_redacted_for_both_execution_branches(self):
        for fixture in (False, True):
            with self.subTest(fixture=fixture):
                stderr = ("\n".join(self.CREDENTIALS) + "\nprovider-fixture-value\ngit-fixture-value").encode()
                self.assertEqual(self.invoke(stderr=stderr, fixture=fixture), "")
                diagnostic = self.companion.read_text(encoding="utf-8")
                for secret in ("synthetic-header", "synthetic-assignment", "synthetic-cli",
                               "synthetic-password", "SYNTHETIC123", "synthetic-bare",
                               *self.environment.values()):
                    self.assertNotIn(secret, diagnostic)
                self.assertIn("redact", diagnostic.lower())
                self.assertEqual(self.log.read_bytes(), self.machine())

    def test_c1_04_stderr_local_and_quoted_paths_redacted(self):
        paths = ('/home/fixture/secret.txt', r'C:\fixture\secret.txt',
                 r'\\fixture\share\secret.txt', '"/home/fixture/space name.txt"',
                 r'"C:\fixture\space name.txt"', str(self.root))
        self.invoke(stderr="\n".join(paths).encode())
        diagnostic = self.companion.read_text(encoding="utf-8")
        for path in paths:
            self.assertNotIn(path, diagnostic)
        self.assertEqual(diagnostic.count("<redacted-path>"), len(paths))

    def test_c1_05_stderr_controls_escaped_with_one_framing_lf(self):
        controls = "\0\r\n\t\x1b\x7f\x80\x9f"
        self.invoke(stderr=("start" + controls + "end").encode())
        raw = self.companion.read_bytes()
        self.assertEqual(raw, b"start\\x00\\x0d\\x0a\\x09\\x1b\\x7f\\x80\\x9fend\n")

    def test_c1_06_machine_bytes_thread_and_digest_binding_preserved(self):
        raw = (b' { "type": "thread.started", "thread_id": "' + self.THREAD.encode()
               + b'" }\r\n' + '{"text":"café 漢字 🚀 /home/fixture C:\\\\fixture \\n \\u001b"}\n'.encode())
        self.invoke(raw, b"/home/fixture/diagnostic\n")
        self.assertEqual(self.log.read_bytes(), raw)
        result = self.run / "result.json"
        result.write_bytes(b"{}\n")
        self.assertEqual(self.driver._record_codex_identity(self.CONTROLLER, self.run, self.run, result), self.THREAD)
        self.assertEqual(self.driver.codex_run_id(self.CONTROLLER), self.THREAD)
        identity = json.loads((self.run / "codex-identity.json").read_bytes())
        self.assertEqual(identity["codex_log_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotIn(b"/home/fixture", self.companion.read_bytes())

    def test_c1_07_tampering_still_fails_digest_verification(self):
        self.invoke(stderr=b"Authorization: Bearer synthetic-tamper")
        result = self.run / "result.json"
        result.write_bytes(b"{}\n")
        self.driver._record_codex_identity(self.CONTROLLER, self.run, self.run, result)
        self.log.write_bytes(self.log.read_bytes() + b'{"text":"changed"}\n')
        with self.assertRaisesRegex(ValidationError, "evidence changed after binding"):
            self.driver.codex_run_id(self.CONTROLLER)
        self.assertNotIn(b"synthetic-tamper", self.companion.read_bytes())

    def test_c1_08_utf8_encoded_boundaries_and_framing(self):
        for character in ("é", "漢", "🚀"):
            stdout = self.machine(character)
            stderr = ("z" + character).encode()
            final_size = len(stdout) + len(stderr) + 1
            with self.subTest(character=character):
                self.retain(stdout, stderr, maximum=final_size)
                self.assertEqual(self.log.read_bytes(), stdout)
                self.assertEqual(self.companion.read_bytes(), stderr + b"\n")
                with self.assertRaisesRegex(ValidationError, "^CODEX_LOG_LIMIT$"):
                    self.retain(stdout, stderr, maximum=final_size - 1)
                self.assertEqual(self.log.read_bytes(), stdout)
                self.assertEqual(self.companion.read_bytes(), stderr + b"\n")

    def test_c1_09_escape_expansion_refused_before_either_write(self):
        stdout, stderr = self.machine(), b"\0" * 16
        with self.assertRaisesRegex(ValidationError, "^CODEX_LOG_LIMIT$"):
            self.retain(stdout, stderr, maximum=len(stdout) + len(stderr) + 1)
        self.assert_no_retention()

    def test_c1_10_credentials_near_read_and_parser_boundaries(self):
        for size in (65520, 65535, 65536, 131070):
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValidationError, "^CODEX_LOG_CREDENTIAL$"):
                    self.invoke(self.machine("é" + "x" * size + " sk-SYNTHETIC123"))
                self.assert_no_retention()

    def test_c1_10a_invalid_record_wins_over_credentials_in_any_record_order(self):
        cases = (
            (b'{"text":"ghp_SYNTHETIC123"}\nnot-json\n', "CODEX_LOG_INVALID"),
            (b'not-json\n{"text":"ghp_SYNTHETIC123"}\n', "CODEX_LOG_INVALID"),
            (b'{"text":"ghp_SYNTHETIC123"}\n', "CODEX_LOG_CREDENTIAL"),
        )
        for raw, code in cases:
            with self.subTest(raw=raw, code=code):
                with self.assertRaisesRegex(ValidationError, "^" + code + "$"):
                    self.invoke(raw)
                self.assert_no_retention()

    def test_c1_11_invalid_machine_input_fails_closed_content_free(self):
        payloads = (b'\xff', b'{"text":"synthetic-invalid"', b'\n', b'null\n',
                    b'{"text":"\\ud800"}\n', b'{"number":NaN}\n',
                    b'{"text":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}\n')
        for raw in payloads:
            with self.subTest(raw_prefix=raw[:30]):
                with self.assertRaisesRegex(ValidationError, "^CODEX_LOG_INVALID$") as caught:
                    self.invoke(raw)
                self.assertNotIn("synthetic-invalid", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                self.assert_no_retention()

    def test_c1_12_inclusive_64_mib_cap_and_one_byte_over(self):
        limit = self.host.CODEX_LOG_LIMIT
        self.assertEqual(limit, 64 * 1024 * 1024)
        raw = b'{}' + b' ' * (limit - 3) + b'\n'
        self.retain(raw)
        self.assertEqual(self.log.stat().st_size, limit)
        self.assertEqual(hashlib.sha256(self.log.read_bytes()).digest(), hashlib.sha256(raw).digest())
        with self.assertRaisesRegex(ValidationError, "^CODEX_LOG_LIMIT$"):
            self.retain(raw + b' ')
        self.assertEqual(self.log.read_bytes(), raw)
        from agentic.child_process import CODEX_LOG_RUNNER_POLICY
        self.assertEqual(CODEX_LOG_RUNNER_POLICY.stdout_limit, 32 * 1024 * 1024)
        self.assertEqual(CODEX_LOG_RUNNER_POLICY.stderr_limit, 8 * 1024 * 1024)
        self.assertEqual(CODEX_LOG_RUNNER_POLICY.aggregate_output_limit, 32 * 1024 * 1024)

    def test_c1_13_preflight_refusals_preserve_existing_evidence_and_no_temporaries(self):
        self.log.write_bytes(b"existing-machine")
        self.companion.write_bytes(b"existing-diagnostic")
        cases = ((self.machine("ghp_SYNTHETIC123"), b"", 1000, "CODEX_LOG_CREDENTIAL"),
                 (self.machine(), b"\0" * 16, len(self.machine()) + 17, "CODEX_LOG_LIMIT"))
        for stdout, stderr, maximum, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(ValidationError, "^" + code + "$"):
                    self.retain(stdout, stderr, maximum=maximum)
                self.assertEqual(self.log.read_bytes(), b"existing-machine")
                self.assertEqual(self.companion.read_bytes(), b"existing-diagnostic")
                self.assertEqual(sorted(path.name for path in self.run.iterdir()),
                                 ["codex.jsonl", "codex.jsonl.stderr"])

    def test_c1_14_nonzero_stays_failure_and_empty_stderr_has_no_companion(self):
        with self.assertRaisesRegex(ValidationError, "HOST_COMMAND_FAILED") as caught:
            self.invoke(stderr=b"Authorization: Bearer synthetic-nonzero", returncode=7)
        self.assertNotIn("synthetic-nonzero", str(caught.exception))
        self.assertNotIn(b"synthetic-nonzero", self.companion.read_bytes())
        self.assertFalse((self.run / "codex-identity.json").exists())
        other_log = self.run / "empty.jsonl"
        self.log = other_log
        self.assertEqual(self.invoke(stdout=b"", stderr=b""), "")
        self.assertEqual(other_log.read_bytes(), b"")
        self.assertFalse(other_log.with_suffix(".jsonl.stderr").exists())
        with self.assertRaisesRegex(ValidationError, "exactly one thread.started"):
            self.driver._codex_thread_id(other_log)
        self.assertFalse((self.run / "codex-identity.json").exists())

    def test_c1_15_agent_qualification_and_first_draft_stop_at_shared_refusal(self):
        templates = self.root / ".agentic/review-loop"
        templates.mkdir(parents=True)
        for name in ("worker-prompt.md", "qualification-probe-prompt.md", "first-draft-worker-prompt.md"):
            (templates / name).write_text("fixture prompt", encoding="utf-8")
        contract = self.root / "contract.md"
        contract.write_text("fixture contract", encoding="utf-8")
        self.driver.c.update(contract_path=str(contract),
                             contract_sha256=hashlib.sha256(contract.read_bytes()).hexdigest())
        result = subprocess.CompletedProcess([], 0, self.machine("ghp_SYNTHETIC123"), b"")
        for route in ("agent", "qualification_agent", "first_draft_worker"):
            with self.subTest(route=route), \
                    mock.patch.object(self.host.os, "environ", self.environment), \
                    mock.patch.object(self.host, "enforce_pinned_awf_skill"), \
                    mock.patch.object(self.host.review_qualification, "validate_config_qualification"), \
                    mock.patch.object(self.host, "run_bounded", return_value=result), \
                    mock.patch.object(self.driver, "first_draft_git_controls", return_value={"fixture": True}), \
                    mock.patch.object(self.driver, "require_first_draft_git_controls"), \
                    mock.patch.object(self.driver, "require_clean_first_draft_checkout"), \
                    mock.patch.object(self.driver, "_bounded_result") as success, \
                    mock.patch.object(self.driver, "_record_codex_identity") as identity:
                success.side_effect = AssertionError("success evidence reached before credential refusal")
                run_id = {"agent": "a", "qualification_agent": "b", "first_draft_worker": "c"}[route] * 8 + "-1111-4111-8111-111111111111"
                with self.assertRaisesRegex(ValidationError, "^CODEX_LOG_CREDENTIAL$"):
                    if route == "agent":
                        self.driver.agent("worker", {}, [], run_id)
                    elif route == "qualification_agent":
                        self.driver.qualification_agent("worker", run_id, {})
                    else:
                        self.driver.first_draft_worker(run_id=run_id)
                success.assert_not_called()
                identity.assert_not_called()
                self.assertEqual(list(self.state.rglob("codex.jsonl*")), [])

    def test_c1_16_unsafe_destinations_and_write_failure_cannot_succeed(self):
        original = self.run / "original"
        original.write_bytes(b"original")
        os.link(original, self.companion)
        with self.assertRaises(ValidationError):
            self.invoke(stderr=b"ordinary")
        self.assertFalse(self.log.exists(), "both destinations must be checked before writing")
        self.assertEqual(original.read_bytes(), b"original")
        with mock.patch.object(safeio.Tree, "write", side_effect=OSError("injected write failure")):
            with self.assertRaisesRegex(ValidationError, "^CODEX_LOG_WRITE$"):
                self.invoke()
        self.assertFalse((self.run / "codex-identity.json").exists())
        self.assertFalse(list(self.run.glob(".awf-*.tmp")))


if __name__ == "__main__":
    unittest.main()
