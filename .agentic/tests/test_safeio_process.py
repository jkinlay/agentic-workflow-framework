"""Process-boundary regressions for the shared bounded runner."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import hashlib
import io
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
from agentic import provenance


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


@contextmanager
def _metadata_reads(before_read=lambda: None):
    """Count real FileIO bytes beneath either pathname or descriptor buffering."""
    observed = {"bytes": 0, "reads": 0, "streams": []}
    real_open, real_fdopen = io.open, os.fdopen

    class CountedRaw(io.RawIOBase):
        def __init__(self, raw):
            self.raw = raw
            observed["streams"].append(self)

        def readable(self):
            return True

        def fileno(self):
            return self.raw.fileno()

        def _before(self):
            if observed["reads"] == 0:
                before_read()
            observed["reads"] += 1

        def read(self, size=-1):
            self._before()
            data = self.raw.read(size)
            observed["bytes"] += len(data)
            return data

        def readinto(self, buffer):
            self._before()
            size = self.raw.readinto(buffer)
            observed["bytes"] += size
            return size

        def close(self):
            try:
                self.raw.close()
            finally:
                super().close()

    def wrap(raw, buffering):
        counted = CountedRaw(raw)
        if buffering == 0:
            return counted
        return io.BufferedReader(counted, io.DEFAULT_BUFFER_SIZE if buffering < 0 else buffering)

    def open_path(file, mode="r", buffering=-1, *args, **kwargs):
        if mode == "rb":
            return wrap(real_open(file, mode, 0, *args, **kwargs), buffering)
        return real_open(file, mode, buffering, *args, **kwargs)

    def open_fd(fd, mode="r", buffering=-1, **kwargs):
        if mode == "rb":
            return wrap(real_open(fd, mode, buffering=0, **kwargs), buffering)
        return real_fdopen(fd, mode, buffering=buffering, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(io, "open", side_effect=open_path))
        stack.enter_context(mock.patch.object(safeio.os, "fdopen", side_effect=open_fd))
        if os.name == "nt":
            real_win_open = safeio.win_open

            def shared_open(path, **kwargs):
                # Permit the same real-file mutation that POSIX permits during a
                # read. Keep all no-follow/type checks in the Windows opener.
                if not kwargs.get("directory", False):
                    kwargs["shared_write"] = True
                return real_win_open(path, **kwargs)

            stack.enter_context(mock.patch.object(safeio, "win_open", side_effect=shared_open))
        yield observed


class RepositoryMetadataReadTests(unittest.TestCase):
    def test_symlinked_metadata_refuses_without_consuming_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "real"
            parent.mkdir()
            target = parent / "config"
            target.write_bytes(b"harmless target")
            linked = root / "linked"
            dangling = root / "dangling"
            linked_parent = root / "linked-parent"
            for case, link, destination, is_directory, path in (
                    ("leaf", linked, target, False, linked),
                    ("dangling", dangling, root / "absent", False, dangling),
                    ("parent", linked_parent, parent, True, linked_parent / "config")):
                with self.subTest(case=case):
                    try:
                        link.symlink_to(destination, target_is_directory=is_directory)
                    except OSError as exc:
                        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                            self.skipTest(f"{case} symlinks unavailable: {exc}")
                        raise
                    with _metadata_reads() as observed:
                        with self.subTest(check="refusal"):
                            with self.assertRaisesRegex(ValidationError, "^REPOSITORY_IDENTITY_INVALID:"):
                                provenance._Git._file_bytes(path)
                        with self.subTest(check="no target bytes"):
                            self.assertEqual(0, observed["bytes"])

    def test_growth_after_observation_is_changed_and_underlying_read_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_bytes(b"safe")

            def grow():
                with open(path, "ab", buffering=0) as writer:
                    writer.write(b"x" * 65536)

            with mock.patch.object(provenance, "_REPOSITORY_FILE_LIMIT", 8), \
                    _metadata_reads(grow) as observed:
                with self.assertRaisesRegex(ValidationError, "^REPOSITORY_IDENTITY_CHANGED:"):
                    provenance._Git._file_bytes(path)
            self.assertGreater(observed["reads"], 0)
            self.assertGreater(observed["bytes"], 0)
            self.assertLessEqual(observed["bytes"], 9)
            self.assertTrue(all(stream.closed and stream.raw.closed
                                for stream in observed["streams"]))

    def test_metadata_ordinary_file_compatibility_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relative in ("missing", "optional-parent/missing"):
                with self.subTest(case=relative):
                    self.assertEqual(b"", provenance._Git._file_bytes(root / relative))
            path = root / "config"
            with mock.patch.object(provenance, "_REPOSITORY_FILE_LIMIT", 4):
                for value in (b"", b"abc", b"1234"):
                    path.write_bytes(value)
                    with self.subTest(case=value):
                        self.assertEqual(value, provenance._Git._file_bytes(path))
                path.write_bytes(b"12345")
                with self.assertRaisesRegex(ValidationError, "^REPOSITORY_METADATA_SIZE:"):
                    provenance._Git._file_bytes(path)

    def test_metadata_nonregular_and_hardlinked_files_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "config"
            target.write_bytes(b"safe")
            hardlink = root / "hardlink"
            os.link(target, hardlink)
            for case, path in (("directory", root), ("hardlink", hardlink)):
                with self.subTest(case=case), self.assertRaisesRegex(
                        ValidationError, "^REPOSITORY_IDENTITY_INVALID:"):
                    provenance._Git._file_bytes(path)

    def test_metadata_unreadable_is_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_bytes(b"safe")
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(io, "open", side_effect=PermissionError("private path")))
                if os.name == "nt":
                    real_open = safeio.win_open

                    def deny_leaf(candidate, **kwargs):
                        if not kwargs.get("directory", False):
                            raise PermissionError("private path")
                        return real_open(candidate, **kwargs)

                    stack.enter_context(mock.patch.object(safeio, "win_open", side_effect=deny_leaf))
                else:
                    real_open = safeio.os.open

                    def deny_leaf(candidate, flags, *args, **kwargs):
                        if candidate == path.name:
                            raise PermissionError("private path")
                        return real_open(candidate, flags, *args, **kwargs)

                    stack.enter_context(mock.patch.object(safeio.os, "open", side_effect=deny_leaf))
                with self.assertRaisesRegex(ValidationError, "^REPOSITORY_IDENTITY_INVALID:") as caught:
                    provenance._Git._file_bytes(path)
            self.assertNotIn("private path", str(caught.exception))

    def test_metadata_disappearance_after_observation_is_not_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_bytes(b"safe")
            real_read, real_snapshot = Path.read_bytes, safeio.snapshot_file

            def vanish_read(candidate):
                candidate.unlink()
                return real_read(candidate)

            def vanish_snapshot(*args, **kwargs):
                path.unlink()
                return real_snapshot(*args, **kwargs)

            with mock.patch.object(Path, "read_bytes", vanish_read), \
                    mock.patch.object(provenance, "snapshot_file", side_effect=vanish_snapshot, create=True):
                with self.assertRaisesRegex(ValidationError, "^REPOSITORY_IDENTITY_INVALID:"):
                    provenance._Git._file_bytes(path)

    def test_metadata_mutation_within_cap_is_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            for case in ("size", "mtime"):
                path.write_bytes(b"safe")
                initial = path.stat()

                def mutate():
                    if case == "size":
                        with open(path, "ab", buffering=0) as writer:
                            writer.write(b"!")
                    else:
                        os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns + 1_000_000_000))

                with self.subTest(case=case), \
                        mock.patch.object(provenance, "_REPOSITORY_FILE_LIMIT", 16), _metadata_reads(mutate):
                    with self.assertRaisesRegex(ValidationError, "^REPOSITORY_IDENTITY_CHANGED:"):
                        provenance._Git._file_bytes(path)

    def test_metadata_pathname_replacement_before_and_after_snapshot_is_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, replacement = root / "config", root / "replacement"
            for stage in ("before", "after"):
                path.write_bytes(b"same")
                replacement.write_bytes(b"same")
                initial = path.stat()
                os.utime(replacement, ns=(initial.st_atime_ns, initial.st_mtime_ns))
                real_read, real_snapshot = Path.read_bytes, safeio.snapshot_file

                def swap():
                    os.replace(replacement, path)

                def replacing_read(candidate):
                    if stage == "before":
                        swap()
                    raw = real_read(candidate)
                    if stage == "after":
                        swap()
                    return raw

                def replacing_snapshot(*args, **kwargs):
                    if stage == "before":
                        swap()
                    result = real_snapshot(*args, **kwargs)
                    if stage == "after":
                        swap()
                    return result

                with self.subTest(stage=stage), mock.patch.object(Path, "read_bytes", replacing_read), \
                        mock.patch.object(provenance, "snapshot_file", side_effect=replacing_snapshot, create=True):
                    with self.assertRaisesRegex(ValidationError, "^REPOSITORY_IDENTITY_CHANGED:"):
                        provenance._Git._file_bytes(path)

    def test_growth_between_inspection_and_descriptor_open_is_changed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_bytes(b"safe")
            real_read, real_snapshot = Path.read_bytes, safeio.snapshot_file

            def grow():
                with open(path, "ab", buffering=0) as writer:
                    writer.write(b"x" * 16)

            def growing_read(candidate):
                grow()
                return real_read(candidate)

            def growing_snapshot(*args, **kwargs):
                grow()
                return real_snapshot(*args, **kwargs)

            with mock.patch.object(provenance, "_REPOSITORY_FILE_LIMIT", 8), \
                    mock.patch.object(Path, "read_bytes", growing_read), \
                    mock.patch.object(provenance, "snapshot_file", side_effect=growing_snapshot, create=True):
                with self.assertRaisesRegex(ValidationError, "^REPOSITORY_IDENTITY_CHANGED:"):
                    provenance._Git._file_bytes(path)

    def test_later_identity_verification_compares_metadata_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_bytes(b"before")
            initial = path.stat()
            git = object.__new__(provenance._Git)
            git._identity = {}
            git._repository_files = {path: provenance._Git._file_bytes(path)}
            path.write_bytes(b"after!")
            os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
            with self.assertRaisesRegex(ValidationError, "^REPOSITORY_IDENTITY_CHANGED:"):
                git._verify_repository_identity()


class MetadataSnapshotContractTests(unittest.TestCase):
    def test_snapshot_bytes_size_digest_identity_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_bytes(b"snapshot")
            with _metadata_reads() as observed, safeio.Tree(path.parent) as tree:
                result = safeio.snapshot_file(tree, path.name, maximum=8)
                self.assertEqual(b"snapshot", result.data)
                self.assertEqual(8, result.size)
                self.assertEqual(hashlib.sha256(b"snapshot").hexdigest(), result.sha256)
                self.assertEqual(safeio._file_identity(path.stat()), result.identity)
            self.assertEqual({}, tree._dirs)
            self.assertTrue(observed["streams"])
            self.assertTrue(all(stream.closed and stream.raw.closed
                                for stream in observed["streams"]))
            self.assertEqual(result, safeio.snapshot_file(path, maximum=8))

    def test_snapshot_stream_creation_failure_closes_descriptor(self):
        captured = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_bytes(b"snapshot")

            def reject(fd, _mode, **_kwargs):
                captured.append(fd)
                raise OSError("fdopen failure")

            try:
                with mock.patch.object(safeio.os, "fdopen", side_effect=reject):
                    with self.assertRaisesRegex(OSError, "fdopen failure"):
                        with safeio.Tree(path.parent) as tree:
                            safeio.snapshot_file(tree, path.name, maximum=8)
                self.assertEqual(1, len(captured))
                with self.assertRaises(OSError):
                    os.fstat(captured[0])
            finally:
                if captured:
                    try:
                        os.close(captured[0])
                    except OSError:
                        pass
        self.assertFalse(Path(directory).exists())

    def test_snapshot_size_refusal_is_typed_and_closes_handles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            path.write_bytes(b"12345")
            with _metadata_reads() as observed:
                with self.assertRaises(ValidationError) as caught:
                    with safeio.Tree(path.parent) as tree:
                        safeio.snapshot_file(tree, path.name, maximum=4)
            self.assertIsInstance(caught.exception, getattr(safeio, "SnapshotSizeError", ()))
            self.assertEqual("Unsafe file type/link count/size", str(caught.exception))
            self.assertEqual(0, observed["bytes"])
            self.assertEqual({}, tree._dirs)
            self.assertTrue(all(stream.closed and stream.raw.closed
                                for stream in observed["streams"]))

    def test_snapshot_growth_and_mutation_refusals_are_typed_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config"
            for value in (b"12345", b"123"):
                path.write_bytes(b"safe")

                def mutate():
                    with open(path, "wb", buffering=0) as writer:
                        writer.write(value)

                with self.subTest(value=value), _metadata_reads(mutate) as observed:
                    with self.assertRaises(ValidationError) as caught:
                        with safeio.Tree(path.parent) as tree:
                            safeio.snapshot_file(tree, path.name, maximum=4)
                    self.assertIsInstance(caught.exception, getattr(safeio, "SnapshotChangedError", ()))
                    self.assertLessEqual(observed["bytes"], 5)
                    self.assertEqual({}, tree._dirs)
                    self.assertTrue(all(stream.closed and stream.raw.closed
                                        for stream in observed["streams"]))


if __name__ == "__main__":
    unittest.main()
