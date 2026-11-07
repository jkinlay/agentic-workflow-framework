"""Independently derived reporting policy with fresh default-ref admission."""
from copy import deepcopy
from threading import RLock
from collections import OrderedDict

from .. import ValidationError
from ..canonical import canonical

from ..budgeting import accepted_budget_snapshot, policy_repository_root
from ..protected_settings import _git


_prepared = OrderedDict()
_MAX_PREPARED = 8
_prepared_lock = RLock()


class ReportingPolicy:
    """Prepare validation before delivery; recheck immutable policy identity at POST.

    Only this instance's independently validated bytes are reusable. A changed,
    absent or unverified symbolic default identity invokes full admission again.
    Git reads retain native-executable, configuration and replacement isolation.
    """
    def __init__(self, root, governance, operating):
        self.root = policy_repository_root(root, operating)
        self.governance = deepcopy(governance)
        self.original = operating
        key = (id(operating), self.root, canonical(self.governance))
        with _prepared_lock:
            prepared = _prepared.get(key)
        # Identity here is an in-process object, never a supplied hash or
        # provenance assertion. Only independently admitted objects enter it.
        # The bounded cache holds no resolved credentials and never skips the
        # fresh Git identity observation, including across controller cycles.
        if prepared is not None and (operating is prepared.snapshot or operating is prepared.original):
            prepared()  # Fresh Git identity observation, even on reuse.
            self.snapshot = prepared.snapshot
        else:
            self.snapshot = accepted_budget_snapshot(self.root, self.governance, operating)
        with _prepared_lock:
            for candidate in (self.original, self.snapshot):
                identity = (id(candidate), self.root, canonical(self.governance))
                _prepared[identity] = self
                _prepared.move_to_end(identity)
            while len(_prepared) > _MAX_PREPARED:
                _prepared.popitem(last=False)

    def _unchanged(self):
        report = self.snapshot.provenance.get('protected_settings', {})
        ref, commit = report.get('ref'), report.get('commit')
        if ref and commit and not report.get('unverified'):
            raw = _git(self.root, 'for-each-ref',
                       '--format=%(refname)%09%(objectname)%09%(symref)', 'refs/remotes/origin/')
            rows = [line.split(b'\t') for line in raw.splitlines()]
            expected = [b'refs/remotes/origin/HEAD', commit.encode(), ref.encode()]
            target = [ref.encode(), commit.encode(), b'']
            return expected in rows and target in rows
        return False

    def __call__(self):
        if self._unchanged():
            return self.snapshot.config
        self.snapshot = accepted_budget_snapshot(self.root, self.governance, self.original)
        return self.snapshot.config


def prepared_snapshot(root, governance, operating):
    """Reuse independently admitted bytes only after a fresh Git identity read.

    A miss preserves the review consumer's existing full, no-refresh admission.
    Neither supplied snapshot hashes nor calendar arguments can populate this
    bounded private cache or substitute for accepted default-branch policy.
    """
    root = policy_repository_root(root, operating)
    key = (id(operating), root, canonical(governance))
    with _prepared_lock:
        prepared = _prepared.get(key)
    if prepared is not None and (operating is prepared.snapshot or operating is prepared.original):
        try:
            if prepared._unchanged():
                return prepared.snapshot
        except ValidationError:
            pass  # Full admission supplies the established refusal and remedy.
    return None
