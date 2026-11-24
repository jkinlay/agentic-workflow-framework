"""Shared Jira closure admission under protected, issue-owned controller context.

The context is process-local trusted-host state. Provider records and adapter
configuration cannot create it. Its history proves only instrumented controller
operations, never which person used a shared credential.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path

from . import ValidationError
from .canonical import fingerprint, loads, timestamp, jira_timestamp
from .shared_owner_policy import (JIRA_SETTING, configured_shared_jira_actor_id,
                                  consumer_policy_root, observe_accepted_owner_policy)

FIELD = 'shared_owner_controller_evidence'
_context = ContextVar('awf_jira_owner_closure', default=None)
_project_context = ContextVar('awf_jira_owner_project_read', default=None)


def context_available():
    value = _context.get()
    return value is not None and value['active']


def accepted_policy_root():
    """A host-rooted project read grants no shared Jira closure authority."""
    if context_available():
        return recheck(need_history=False)['root']
    value = _project_context.get()
    _require(value is not None and value['active'], 'Jira project policy context is unavailable')
    _require(_policy(value['root'], value['config']) == value['policy'], 'Jira project policy changed before use')
    return value['root']


def project_context_available():
    value = _project_context.get()
    return context_available() or value is not None and value['active']


@contextmanager
def project_policy_scope(store, config):
    """Bind accepted project reads independently of either account opt-out."""
    from .shared_owner_policy import configured_shared_owner_actor_id
    if (configured_shared_jira_actor_id(config) is None
            and configured_shared_owner_actor_id(config) is None):
        yield
        return
    root = consumer_policy_root(root=getattr(store, 'accepted_policy_root', None), store=store)
    observation = _policy(root, config)
    value = {'active': True, 'root': root, 'config': deepcopy(config), 'policy': observation}
    token = _project_context.set(value)
    try:
        yield
        accepted_policy_root()
    finally:
        value['active'] = False
        _project_context.reset(token)


def require_current_issue_policy(policy):
    """The adapter re-observes keyword and leaf scope on retained reads too."""
    context = recheck()
    from .jira_lifecycle import owner_closure_required, require_jira_issue_scope
    _require(policy.get('issue_key') == context['issue'] and policy.get('issue_type') == 'LEAF'
             and owner_closure_required(context['config'], policy['snapshot']),
             'Shared Jira closure no longer has an owner-closure keyword on a leaf ticket')
    require_jira_issue_scope(context['config'], context['issue'], policy['snapshot'])


def _require(condition, message):
    if not condition:
        raise ValidationError(message)


def _policy(root, config):
    from .contracts import Contracts
    from .lifecycle import definition
    from .policy import policy_hash
    _require(root is not None, 'Shared Jira closure needs accepted default-branch policy')
    workflow = definition()
    return observe_accepted_owner_policy(root, config, workflow,
        Contracts(Path(__file__).resolve().parents[2] / 'schemas'),
        policy_sha256=policy_hash(config, workflow))


def _journal(store, config, issue):
    from .continuous_controller import (ContinuousControllerStore, _jira_ticket_operations,
        _require_jira_operation_identity, _jira_operator_resolution, jira_assignment_state,
        _jira_lifecycle_binding_identity, _jira_record_provider)
    from .provider_identity import expected_jira_identity
    _require(isinstance(store, ContinuousControllerStore), 'Shared Jira closure needs protected controller history')
    _require(store.path.is_file(), 'Shared Jira controller history is missing')
    state = jira_assignment_state(store)
    _require(not state.get('jira_assignment_write_stops', {}).get(issue),
             'Shared Jira closure has unresolved assignment history')
    rows = _jira_ticket_operations(store, issue)
    _require(rows, 'Shared Jira controller operation history is missing')
    expected = expected_jira_identity(config)
    provider = {key: expected[source] for key, source in (
        ('cloud_id', 'cloud_id'), ('site', 'site'), ('project_id', 'project_id'),
        ('project_key', 'project_key'), ('controller_actor_id', 'account_id'))}
    from .intake_assignment import verify_assignment_record
    for assignment in state.get('tier_assignment_history', {}).get(issue, []):
        verify_assignment_record(assignment)
        identity = assignment.get('jira_identity', {})
        _require(identity.get('jira_provider') == provider and identity.get('issue_id') == issue
            and identity.get('project_id') == config.get('project', {}).get('id')
            and identity.get('repository_id') == config.get('github', {}).get('repository_id'),
            'Shared Jira assignment history belongs to another identity')
    with store.connection() as db:
        _require(db.execute('PRAGMA quick_check').fetchall()[0][0] == 'ok',
                 'Shared Jira controller history is corrupt')
        # Both journal families must be readable. A newly created controller
        # journal can have no batch table until its first T11 invocation.
        result = []
        pinned = []
        for pin_row in db.execute("SELECT key,value FROM controller_meta WHERE key LIKE 'jira-identity:%'"):
            value = loads(pin_row['value'])
            if value['identity']['issue_id'] == issue:
                _require(pin_row['key'] == 'jira-identity:' + value['operation_id'],
                         'Shared Jira controller identity history is corrupt')
                pinned.append(value['operation_id'])
        _require(sorted(pinned) == sorted(row['operation_id'] for row, _ in rows),
                 'Shared Jira controller operation history is incomplete')
        for row, table in rows:
            record = loads(row['record_json'])
            _require(record['operation_id'] == row['operation_id']
                and record['binding']['issue_id'] == row['issue_id'] == issue
                and record['lifecycle_event'] == row['event']
                and fingerprint('controller-jira-operation', {
                    k: v for k, v in record.items() if k != 'created_at'}) == row['intent_sha256'],
                'Shared Jira controller intent is corrupt')
            _require_jira_operation_identity(store, config, row, record)
            pin = db.execute('SELECT value FROM controller_meta WHERE key=?',
                             ('jira-identity:' + row['operation_id'],)).fetchone()
            _require(pin is not None and loads(pin['value'])['intent_sha256'] == row['intent_sha256'],
                     'Shared Jira controller intent identity is incomplete')
            identity = loads(pin['value'])['identity']
            binding = _jira_lifecycle_binding_identity(record['binding'], config)
            _require(_jira_record_provider(record) == provider
                and all(binding[key] == identity[key] for key in ('project_id', 'repository_id', 'issue_id')),
                'Shared Jira controller history belongs to another identity')
            _require(_jira_operator_resolution(store, config, row) is None,
                     'Shared Jira closure has an abandoned controller operation')
            _require(row['status'] in {'PENDING', 'HELD', 'SUCCEEDED'},
                     'Shared Jira closure has attempted, unknown or in-flight controller history')
            _require(row['status'] != 'PENDING' or row['operation_json'] is None,
                     'Pending Jira intent has an attempted write receipt')
            if row['status'] == 'HELD':
                held = db.execute('SELECT value FROM controller_meta WHERE key=?',
                    ('jira-held-before-write:' + row['operation_id'],)).fetchone()
                _require(held is not None and loads(held['value']) == {
                    'operation_id': row['operation_id'], 'intent_sha256': row['intent_sha256'],
                    'identity': loads(pin['value'])['identity'], 'mutation_attempted': False}
                    and row['operation_json'] is None,
                    'Held Jira intent lacks proof that no write was attempted')
            if record['to_status_id'] == config['jira']['status_map']['done']:
                _require(row['status'] == 'HELD',
                         'Shared Jira closure has a Done intent whose attempt status is not proven unsent')
            # Read every retained receipt, including successful non-Done writes.
            for column in ('operation_json', 'readback_json'):
                if row[column] is not None:
                    _require(isinstance(loads(row[column]), dict), 'Shared Jira controller receipt is corrupt')
            if row['status'] == 'SUCCEEDED':
                observation = loads(row['readback_json']) if row['readback_json'] is not None else None
                _require(isinstance(observation, dict)
                    and observation.get('issue_id') == issue
                    and observation.get('jira_provider') == loads(pin['value'])['identity']['jira_provider']
                    and observation.get('status_id', observation.get('status')) == record['to_status_id']
                    and timestamp(observation.get('observed_at')) >= timestamp(record['created_at']),
                    'Shared Jira successful operation lacks complete readback history')
                if row['operation_json'] is not None:
                    operation = loads(row['operation_json'])
                    _require(operation.get('operation_id') == row['operation_id']
                        and operation.get('issue_id') == issue and operation.get('status') == 'ATTEMPTED'
                        and operation.get('jira_provider') == observation['jira_provider']
                        and observation.get('actor') == config['jira']['controller_actor_id']
                        and timestamp(record['created_at']) <= timestamp(operation.get('observed_at'))
                            <= timestamp(observation['observed_at']),
                        'Shared Jira successful operation has corrupt attempt history')
            result.append({'table': table, **row, 'identity': loads(pin['value'])})
    return fingerprint('shared-jira-controller-history', {'issue_id': issue, 'operations': result,
                       'assignment_history': state.get('tier_assignment_history', {}).get(issue, [])})


@contextmanager
def closure_scope(store, config, issue, contract, policy, *, owned=False):
    """Called by the trusted controller, never selected by a provider body."""
    from .continuous_controller import _batch_jira_ownership
    from .jira_lifecycle import owner_closure_required
    if configured_shared_jira_actor_id(config) is None:
        yield
        return
    root = consumer_policy_root(root=getattr(store, 'accepted_policy_root', None), store=store)
    if not (contract.get('owner_closure_required') is True and policy.get('issue_type') == 'LEAF'
            and owner_closure_required(config, policy['snapshot'])):
        # Distinct-owner evidence still follows its original validation path.
        # The same-account adapter refusal remains in force without a context.
        yield
        return

    @contextmanager
    def admission():
        accepted = _policy(root, config)
        _require(accepted.get(JIRA_SETTING) == config['jira']['controller_actor_id'],
                 'Accepted Jira shared account differs from the controller')
        value = {'active': True, 'store': store, 'config': deepcopy(config), 'issue': issue, 'root': root,
                 'policy': accepted, 'history': None, 'snapshot': deepcopy(policy['snapshot'])}
        token = _context.set(value)
        try:
            yield
            # Linearize acceptance against durable claims, which are created
            # before writers try to obtain the shared issue lock.
            if value['history'] is not None:
                with store.transaction():
                    recheck()
            else:
                # A distinct owner's unchanged proof does not use the exception.
                recheck(need_history=False)
        finally:
            value['active'] = False
            _context.reset(token)

    if owned:
        with admission():
            yield
    else:
        with _batch_jira_ownership(store, issue_id=issue) as locked:
            _require(locked, 'Shared Jira closure has a live controller writer')
            with admission():
                yield


def recheck(*, need_history=True):
    context = _context.get()
    _require(context is not None and context['active'], 'Shared Jira closure needs protected issue-owned controller context')
    _require(_policy(context['root'], context['config']) == context['policy'],
             'Shared Jira owner policy changed before use')
    if need_history:
        history = _journal(context['store'], context['config'], context['issue'])
        if context['history'] is None:
            context['history'] = history
        else:
            _require(history == context['history'], 'Shared Jira controller history changed before use')
    return context


def annotate(proof, history, provider_updated_at):
    """Retain raw, bounded provider history outside signed or transition bodies."""
    context = recheck()
    _require(proof['issue_id'] == context['issue']
             and proof['actor'] == context['config']['jira']['controller_actor_id'],
             'Shared Jira closure actor or issue differs from accepted policy')
    result = deepcopy(proof)
    result[FIELD] = {'shared_owner_controller_account': True,
        'owner_policy_sha256': context['policy']['policy_sha256'],
        'accepted_owner_policy': deepcopy(context['policy']), 'controller_history_sha256': context['history'],
        'owner_closure_required': True, 'snapshot': deepcopy(context['snapshot']),
        'history': deepcopy(history), 'provider_updated_at': provider_updated_at}
    return result


def validate(proof):
    """Revalidate an adapter projection independently at controller admission."""
    context = recheck()
    from .provider_identity import expected_jira_identity
    expected = expected_jira_identity(context['config'])
    _require(proof.get('jira_provider') == {key: expected[source] for key, source in (
        ('cloud_id', 'cloud_id'), ('site', 'site'), ('project_id', 'project_id'),
        ('project_key', 'project_key'), ('controller_actor_id', 'account_id'))},
        'Shared Jira closure provider differs from accepted policy')
    evidence = validate_history(proof)
    _require(evidence['accepted_owner_policy'] == context['policy']
        and evidence['controller_history_sha256'] == context['history']
        and evidence['snapshot'] == context['snapshot'], 'Shared Jira closure policy or history changed')
    _require(proof['actor'] == context['config']['jira']['controller_actor_id']
        and proof['status'] == context['config']['jira']['status_map']['done'],
        'Shared Jira closure differs from accepted controller or Done status')
    return evidence


def validate_history(proof):
    """Check a protected historical fact, never grant current closure authority.

    Old accepted closures remain identity history after policy or journal
    changes. Reusing one still requires validate() under a fresh owned scope.
    """
    evidence = proof.get(FIELD)
    _require(isinstance(evidence, dict) and set(evidence) == {
        'shared_owner_controller_account', 'owner_policy_sha256', 'accepted_owner_policy',
        'controller_history_sha256', 'owner_closure_required', 'snapshot', 'history', 'provider_updated_at'},
        'Shared Jira closure evidence is incomplete')
    from .contracts import Contracts
    Contracts(Path(__file__).resolve().parents[2] / 'schemas').validate('jira-shared-owner-evidence', evidence)
    policy = evidence['accepted_owner_policy']
    from .shared_owner_policy import SETTING, shared_owner_warnings
    _require(evidence['shared_owner_controller_account'] is True
        and evidence['owner_closure_required'] is True
        and evidence['owner_policy_sha256'] == policy['policy_sha256']
        and policy[JIRA_SETTING] == proof['actor'] == proof['jira_provider']['controller_actor_id']
        and policy['warnings'] == shared_owner_warnings({
            'merge_gate': {SETTING: policy[SETTING]}, 'jira': {JIRA_SETTING: policy[JIRA_SETTING]}}),
        'Shared Jira historical closure differs from its policy or controller')
    page = evidence['history']
    histories = page.get('histories') if isinstance(page, dict) else None
    _require(isinstance(histories, list) and 0 < len(histories) <= 100
        and type(page.get('startAt')) is int and page['startAt'] == 0
        and type(page.get('total')) is int and page['total'] == len(histories)
        and ('isLast' not in page or page['isLast'] is True),
        'Shared Jira closure needs complete provider history without pagination gaps')
    ordered, identities = [], set()
    from datetime import timedelta
    import re
    for item in histories:
        _require(isinstance(item, dict) and isinstance(item.get('id'), str)
                 and re.fullmatch('[0-9]+', item['id']) is not None
                 and int(item['id']) not in identities and isinstance(item.get('items'), list),
                 'Shared Jira provider history is corrupt')
        identities.add(int(item['id']))
        ordered.append((int(item['id']), jira_timestamp(item.get('created')), item))
    latest, floor = None, None
    for _, created, item in sorted(ordered):
        _require(floor is None or created >= floor - timedelta(seconds=300),
                 'Shared Jira provider history order is corrupt')
        floor = max(floor, created) if floor is not None else created
        statuses = [entry for entry in item['items'] if isinstance(entry, dict) and entry.get('field') == 'status']
        _require(len(statuses) <= 1, 'Shared Jira provider history has ambiguous status transitions')
        if statuses:
            actor = item.get('author', {}).get('accountId')
            _require(isinstance(actor, str) and actor.strip() and actor == actor.strip()
                     and isinstance(statuses[0].get('to'), str), 'Shared Jira provider actor history is incomplete')
            latest = (item, statuses[0], actor, created)
    _require(latest is not None, 'Shared Jira closure has no status transition')
    item, transition, actor, created = latest
    _require(actor == proof['actor']
        and item['id'] == proof['history_id'] and transition.get('from') == proof['from_status']
        and transition['to'] == proof['status']
        and created == timestamp(proof['transition_at'])
        and created <= jira_timestamp(evidence['provider_updated_at']) <= timestamp(proof['observed_at']),
        'Shared Jira closure differs from the latest provider transition')
    return evidence


@contextmanager
def retained_scope(store, config, value, *, owned=False):
    """Only a closure already retained in this protected journal supplies context."""
    proof = value['owner_closure']
    if FIELD not in proof:
        yield
        return
    with store.connection() as db:
        row = db.execute('SELECT value FROM controller_meta WHERE key=?',
            ('jira-owner-closure:' + value['record']['record_id'],)).fetchone()
    _require(row is not None, 'Shared Jira retained closure is missing')
    retained = loads(row['value'])
    _require({k: v for k, v in retained.items() if k != 'identity'} == value,
             'Shared Jira closure differs from protected retained evidence')
    from .continuous_controller import _jira_operation_identity, _jira_lifecycle_binding_identity
    identity = retained.get('identity')
    binding = _jira_lifecycle_binding_identity(value['record']['binding'], config)
    _require(identity == _jira_operation_identity(config, value['record'])
             and all(binding[key] == identity[key] for key in ('project_id', 'repository_id', 'issue_id')),
             'Shared Jira retained closure belongs to another identity')
    evidence = proof[FIELD]
    with closure_scope(store, config, proof['issue_id'],
            {'owner_closure_required': evidence['owner_closure_required']},
            {'issue_type': 'LEAF', 'snapshot': evidence['snapshot']}, owned=owned):
        validate(proof)
        yield
