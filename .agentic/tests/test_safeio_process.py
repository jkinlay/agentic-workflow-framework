"""Process-boundary regressions for the shared bounded runner."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from agentic import ValidationError
from agentic import safeio


def _python(source, *arguments):
    return [sys.executable, "-B", "-c", source, *map(str, arguments)]


class BoundedProcessTests(unittest.TestCase):
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
            with self.subTest(reason=reason), self.assertRaisesRegex(ValidationError, reason):
                safeio.run_bounded(
                    _python(source), stdout_limit=4, stderr_limit=4,
                    aggregate_output_limit=8,
                )

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
        read_fd, write_fd = os.pipe()
        try:
            os.set_inheritable(write_fd, True)
            source = (
                "import os,sys; fd=int(sys.argv[1]); "
                "\ntry: os.fstat(fd)\nexcept OSError: sys.exit(0)\nsys.exit(9)"
            )
            result = safeio.run_bounded(_python(source, write_fd))
            self.assertEqual(result.returncode, 0)
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


if __name__ == "__main__":
    unittest.main()
