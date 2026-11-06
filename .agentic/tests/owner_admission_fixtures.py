"""Synthetic pre-existing owner facts for host/usage integration fixtures.

These tests already double external provider/host observations. Give their
persisted settings explicit protected confirmation records too; never replace
the production load validator. The installed workflow subprocess tests exercise
terminal authentication, fake gh, consumption and publication end to end.
"""
from datetime import timedelta
from dataclasses import replace
from pathlib import Path
import tempfile
import shutil
import subprocess
import uuid
from unittest.mock import patch

from agentic import owner_admission as admission
from agentic.canonical import canonical, now_text, sha256, timestamp


def confirmed_fixture_snapshot(case, root, value, governance):
    """Synthetic host choice with an explicit protected confirmation fact.

    The historical host/usage tests construct modes in memory. Their fixtures
    must now establish admission for OFF transitions as well as enabled modes.
    Production validation still runs in the consumer; no gate is replaced.
    """
    from agentic.operating import validate_operating
    record_fixture_owner_confirmation(case, root, governance, value)
    return replace(validate_operating(value, governance),
                   _provenance_json=canonical({'path': str(Path(root) / 'OPERATING_CONFIG.yaml')}))


def record_fixture_owner_confirmation(case, root, governance, value):
    if not (Path(root) / '.git').exists():
        subprocess.run([shutil.which('git'), 'init', '-q', str(root)], check=True, capture_output=True)
    if not hasattr(case, '_fixture_owner_home'):
        temporary = tempfile.TemporaryDirectory(prefix='awf-owner-fact-')
        case.addCleanup(temporary.cleanup)
        case._fixture_owner_home = Path(temporary.name)
        location = patch.object(admission, '_owner_state_home', return_value=case._fixture_owner_home)
        location.start()
        case.addCleanup(location.stop)
    created = now_text()
    record = dict(format='awf-owner-proposal-1', request_id=str(uuid.uuid4()),
        binding=dict(project_id=governance['project']['id'],
            git_common_identity=admission.git_common_identity(root),
            project_location_hash=sha256(str(Path(root).resolve()).encode()),
            repository={key: governance['github'][key]
                        for key in ('host', 'repository_id', 'repository', 'base_branch')}),
        created_at=created,
        expires_at=(timestamp(created) + timedelta(seconds=admission.TTL_SECONDS)).isoformat().replace('+00:00', 'Z'),
        protected_settings=admission.protected_settings(value),
        protected_values={name: value[name] for name, _ in admission.PROTECTED_DOMAINS if name in value})
    store = admission.ProposalStore.for_project(root, governance['project']['id'])
    digest = store.save(record)
    audit = dict(actor_id=1001, login='fixture-owner', type='User', digest=digest,
        request_id=record['request_id'], confirmed_at=created,
        executable_path='fixture-native-gh', executable_sha256=sha256(b'fixture-native-gh'))
    with store.transaction() as db:
        store.consume(digest, audit, db)
    event = dict(id='C-' + uuid.uuid4().hex, owner_admission=audit)
    admission.register_transaction(root, governance, event, canonical(dict(fixture_event=event)))
    admission.complete_transaction(root, governance, event)
