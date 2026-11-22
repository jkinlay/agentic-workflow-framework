"""Immutable default-branch policy bytes for owner-identity unit fixtures."""
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
import tempfile
from unittest.mock import patch

from agentic.canonical import canonical, fingerprint, load, sha256
from agentic.lifecycle import definition


_DEFAULT_ACTOR = object()


class AcceptedOwnerFixture:
    def __init__(self, test, *, owners=(101, 102, 202), actor=_DEFAULT_ACTOR, root=None):
        # Keep the pre-existing merge-train fixture's legacy login/numeric-owner
        # cases on null-account policy. Other suites exercise a configured,
        # distinct automation actor. Explicit actor arguments always win.
        if actor is _DEFAULT_ACTOR:
            actor = (None if any(base.__name__ == 'MergeTrainTests'
                                 and base.__module__ in {'test_merge_train', '__main__'}
                                 for base in type(test).__mro__) else 1002)
        folder = tempfile.TemporaryDirectory()
        test.addCleanup(folder.cleanup)
        self.root = Path(root or folder.name).resolve()
        self.config = load(Path(__file__).resolve().parents[2] / '.agentic/examples/PROJECT_CONFIG.yaml')
        self.config['github']['expected_actor_id'] = actor
        self.config['merge_gate']['trusted_owner_ids'] = list(owners)
        self.config['merge_gate']['shared_owner_automation_actor_id'] = actor if actor in owners else None
        self.workflow = definition()
        self.stack = ExitStack()
        test.addCleanup(self.stack.close)
        from agentic import shared_owner_policy, protected_settings
        old_git, old_blob, old_ref = (shared_owner_policy._git, shared_owner_policy._blob,
                                       shared_owner_policy._default_ref)
        old_protected_git = protected_settings._git

        def matching(root):
            return Path(root).resolve() == self.root

        def git(root, *args, **kwargs):
            if not matching(root):
                return old_git(root, *args, **kwargs)
            if args[0] == 'rev-parse':
                return b'a' * 40
            if args[0] == 'ls-tree':
                return b'100644 blob ' + b'f' * 40 + b'\t' + args[-1].encode()
            if args[:3] == ('config', '--local', '--no-includes'):
                github = self.config['github']
                return (github['host'] + '/' + github['repository'] + '.git\0').encode()
            test.fail('Unexpected immutable identity read: ' + repr(args))

        def blob(root, commit, path):
            if not matching(root):
                return old_blob(root, commit, path)
            return {'.agentic/PROJECT_CONFIG.yaml': canonical(self.config),
                    '.agentic/workflow.yaml': canonical(self.workflow)}.get(path)

        self.stack.enter_context(patch.object(shared_owner_policy, '_default_ref',
            side_effect=lambda root: ('refs/remotes/origin/main', 'fixture') if matching(root) else old_ref(root)))
        self.stack.enter_context(patch.object(shared_owner_policy, '_git', side_effect=git))
        self.stack.enter_context(patch.object(protected_settings, '_git',
            side_effect=lambda root, *args, **kwargs: git(root, *args, **kwargs) if matching(root)
                        else old_protected_git(root, *args, **kwargs)))
        self.stack.enter_context(patch.object(shared_owner_policy, '_blob', side_effect=blob))

    @property
    def config_sha256(self):
        return sha256(canonical(self.config))

    def projection(self, owners=None):
        payload = {'config_sha256': self.config_sha256,
                   'trusted_owner_ids': deepcopy(self.config['merge_gate']['trusted_owner_ids']
                                                if owners is None else list(owners)),
                   'expected_actor_id': self.config['github']['expected_actor_id']}
        return {**payload, 'binding_sha256': fingerprint('provenance_owner_config', payload)}
