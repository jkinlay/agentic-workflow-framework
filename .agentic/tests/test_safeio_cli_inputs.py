"""Exercise input boundaries through the real CLI dispatch functions."""
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import source_only  # noqa: E402,F401
from agentic import cli, publication, safeio

SPEC = importlib.util.spec_from_file_location(
    "cli_input_review_loop", Path(__file__).resolve().parents[1] / "scripts/review_loop.py")
review_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review_cli)

DISPOSITION_CAP = 65_536
RENDER_CAP = 1_048_576


@contextmanager
def observe_reads(path, growth=None):
    """Retain real reads, recording bounds and growing after the size check."""
    original_open, original_fdopen = io.open, os.fdopen
    evidence = {"requests": [], "bytes": 0}

    class Stream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            evidence["requests"].append(size)
            if growth is not None:
                with original_open(path, "ab") as writer:
                    writer.write(growth)
            data = self.stream.read(size)
            evidence["bytes"] += len(data)
            return data

    def opened(file, *args, **kwargs):
        stream = original_open(file, *args, **kwargs)
        return Stream(stream) if not isinstance(file, int) and Path(file) == path else stream

    with ExitStack() as stack:
        stack.enter_context(patch("io.open", side_effect=opened))
        stack.enter_context(patch(
            "os.fdopen", side_effect=lambda *a, **k: Stream(original_fdopen(*a, **k))))
        if os.name == "nt" and growth is not None:
            original_win_open = safeio.win_open

            def shared_open(*args, **kwargs):
                # Permit a deterministic concurrent writer for this race fixture;
                # retain the real Windows link/type checks and real bounded read.
                return original_win_open(*args, **kwargs, shared_write=True)

            stack.enter_context(patch.object(safeio, "win_open", side_effect=shared_open))
        yield evidence


class CliInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="awf-cli-input-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "input.txt"

    def resume(self, disposition=True):
        config = {"runtime_manifest_sha256": "digest", "state_dir": "state",
                  "repository_id": 1, "first_draft": False}
        driver, store = Mock(), Mock()
        store.db.execute.return_value.fetchone.return_value = None
        candidate = {"head": "candidate"}
        driver.snapshot.return_value = candidate
        consumer = Mock(return_value={"status": "OK"})
        args = ["--config", "config.json", "resume", "--reconciled-run", "none"]
        if disposition:
            args += ["--disposition", str(self.input)]
        out, err = io.StringIO(), io.StringIO()
        with ExitStack() as stack:
            for name, value in (("verify_installed", Mock(return_value="digest")),
                                ("load_config", Mock(return_value=config)),
                                ("HostDriver", Mock(return_value=driver)),
                                ("LoopStore", Mock(return_value=store)),
                                ("resume", consumer)):
                stack.enter_context(patch.object(review_cli, name, value))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            code = review_cli.main(args)
        driver.preflight.assert_called_once_with(candidate)
        store.close.assert_called_once_with()
        return code, out.getvalue(), err.getvalue(), consumer

    def render(self, mapping_effect=None):
        out, err = io.StringIO(), io.StringIO()
        # Keep the real renderer, so valid-output controls cover its behavior.
        with patch.object(cli, "configure_streams"), patch.object(
                publication, "load_mapping", side_effect=mapping_effect,
                return_value=({}, "digest", None)), patch.object(
                publication, "render_aliases", wraps=publication.render_aliases) as consumer, \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--root", str(self.root), "publication-render",
                             "--input", str(self.input)])
        return code, out.getvalue(), err.getvalue(), consumer

    def assert_refused(self, result):
        code, out, err, consumer = result
        self.assertEqual(code, 2, (out[:200], err[:200]))
        consumer.assert_not_called()
        self.assertTrue(err)

    def check_oversize(self, cap, invoke, seed):
        for growing in (False, True):
            with self.subTest(growing=growing):
                self.input.write_bytes(seed if growing else seed + b" " * (cap + 1))
                with observe_reads(self.input, b" " * (cap + 1) if growing else None) as evidence:
                    result = invoke()
                self.assert_refused(result)
                self.assertTrue(all(0 <= size <= cap + 1 for size in evidence["requests"]))
                self.assertLessEqual(evidence["bytes"], cap + 1)
                if growing:
                    self.assertEqual(evidence["requests"], [cap + 1])
                    self.assertEqual(evidence["bytes"], cap + 1)
                else:
                    self.assertEqual(evidence["requests"], [])
                if invoke == self.render:
                    self.assertEqual(result[1], "")

    def test_resume_disposition_refuses_oversize_before_unbounded_read(self):
        self.check_oversize(DISPOSITION_CAP, self.resume, b'{"decision":"PARK"}')

    def test_resume_disposition_rejects_duplicate_keys(self):
        for data in (b'{"decision":"PARK","decision":"RESCOPE"}',
                     b'{"decision":"PARK","nested":{"x":1,"x":2}}'):
            with self.subTest(data=data):
                self.input.write_bytes(data)
                result = self.resume()
                self.assert_refused(result)
                self.assertIn("duplicate", result[2].lower())

    def test_publication_render_refuses_oversize_before_unbounded_read(self):
        self.check_oversize(RENDER_CAP, self.render, b"render me")

    def test_publication_render_rejects_link_replacement(self):
        self.input.write_bytes(b"original")
        target = self.root / "target.txt"
        target.write_bytes(b"linked target must never render")

        def replace_before_open(*args):
            self.input.unlink()
            # A hard link is portable and requires no Windows symlink privilege.
            os.link(target, self.input)
            return {}, "digest", None

        with observe_reads(self.input) as evidence:
            result = self.render(replace_before_open)
        self.assert_refused(result)
        self.assertEqual(result[1], "")
        self.assertEqual(evidence["bytes"], 0)

    def test_resume_disposition_exact_cap_and_cap_plus_one(self):
        data = b'{"decision":"PARK"}'
        self.input.write_bytes(data + b" " * (DISPOSITION_CAP - len(data)))
        result = self.resume()
        self.assertEqual(result[0], 0, result[2])
        self.assertEqual(result[3].call_args.args[-1], {"decision": "PARK"})
        self.input.write_bytes(self.input.read_bytes() + b" ")
        self.assert_refused(self.resume())

    def test_resume_disposition_valid_and_absent(self):
        value = {"decision": "PARK", "owner": "owner", "open_finding_ids": ["F1"]}
        self.input.write_text(json.dumps(value), encoding="utf-8")
        result = self.resume()
        self.assertEqual(result[0], 0, result[2])
        self.assertEqual(result[3].call_args.args[-1], value)
        self.input.unlink()
        result = self.resume(disposition=False)
        self.assertEqual(result[0], 0, result[2])
        self.assertIsNone(result[3].call_args.args[-1])

    def test_resume_disposition_rejects_non_object_and_invalid_utf8(self):
        for data in (b"[]", b'"PARK"', b'{"decision":"\xff"}'):
            with self.subTest(data=data):
                self.input.write_bytes(data)
                self.assert_refused(self.resume())

    def test_publication_render_exact_cap_and_cap_plus_one(self):
        self.input.write_bytes(b"x" * RENDER_CAP)
        result = self.render()
        self.assertEqual(result[0], 0, result[2])
        self.assertEqual(result[1], "x" * RENDER_CAP)
        result[3].assert_called_once()
        self.input.write_bytes(b"x" * (RENDER_CAP + 1))
        result = self.render()
        self.assert_refused(result)
        self.assertEqual(result[1], "")

    def test_publication_render_preserves_output_and_replacement_decoding(self):
        self.input.write_bytes(b"hello\r\nworld\xff\n")
        result = self.render()
        self.assertEqual(result[0], 0, result[2])
        self.assertEqual(result[1], "hello\nworld\ufffd\n")


if __name__ == "__main__":
    unittest.main()
