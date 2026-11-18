"""Immutable Git I/O fixture for ordinary intake-owner compatibility tests."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
from unittest.mock import patch

from agentic.canonical import canonical
from agentic.lifecycle import definition


@contextmanager
def accepted_intake_policy(config, root=None):
    """Supply accepted bytes, without replacing policy validation or decisions."""
    with tempfile.TemporaryDirectory(prefix="intake-policy-") as folder:
        project = Path(root) if root is not None else Path(folder)
        accepted = {key: value for key, value in config.items() if key != "_awf_runtime"}
        blobs = {".agentic/PROJECT_CONFIG.yaml": canonical(accepted),
                 ".agentic/workflow.yaml": canonical(definition())}
        def git(observed_root, *args, **kwargs):
            if Path(observed_root) != project:
                raise AssertionError("Policy read escaped the fixture root")
            if args[0] == "rev-parse":
                return b"a" * 40
            if args[0] == "ls-tree":
                return b"100644 blob " + b"f" * 40 + b"\t" + args[-1].encode()
            if args[:3] == ("config", "--local", "--no-includes"):
                github = accepted["github"]
                return (github["host"] + "/" + github["repository"] + ".git\0").encode()
            raise AssertionError("Unexpected immutable Git read")
        with patch("agentic.shared_owner_policy._default_ref", return_value=(
                "refs/remotes/origin/" + accepted["github"]["base_branch"], "fixture")), \
                patch("agentic.shared_owner_policy._git", side_effect=git), \
                patch("agentic.protected_settings._git", side_effect=git), \
                patch("agentic.shared_owner_policy._blob", side_effect=lambda root, commit, path: blobs.get(path)):
            yield project


def change_assignment_with_policy(config, *args, **kwargs):
    from agentic.intake_assignment import change_assignment
    roots = tuple(getattr(kwargs.get("store"), "worktree_roots", ()))
    with accepted_intake_policy(config, roots[0] if len(roots) == 1 else None) as root:
        return change_assignment(config, *args, accepted_policy_root=root, **kwargs)
