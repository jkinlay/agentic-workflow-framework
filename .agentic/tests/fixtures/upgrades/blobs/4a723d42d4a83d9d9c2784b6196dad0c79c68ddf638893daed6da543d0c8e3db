"""Whole-project continuation decisions and unsent authorization presentation.

The native coordinator supplies current, verified facts and executes the returned
actions. This module neither launches agents nor creates a background scheduler.
It cannot authenticate an owner, grant permission, merge, or submit a chat message.
"""
from copy import deepcopy
from urllib.parse import urlsplit

from .canonical import fingerprint, fresh, timestamp
from .interaction import decide_action, gate_handoff, next_step, require, status_report, text_field


def fields(value, names, label):
    require(isinstance(value, dict) and set(value) == set(names.split()), f'{label}: missing or unknown fields')


def _text(value, label):
    text_field(value, label)
    require(not any(ord(c) < 32 for c in value), f'{label} must be single-line text')
    return value


def _authorization(item, contracts, now):
    require(isinstance(item, dict) and 'kind' in item, 'Invalid authorization entry')
    if item['kind'] == 'candidate_merge':
        fields(item, 'id kind gate request config', 'Candidate authorization')
        report = gate_handoff(item['gate'], item['request'], item['config'], contracts, now)
        expiry = item['request']['expires_at']
    else:
        fields(item, 'id kind decision expires_at', 'Action authorization')
        require(item['kind'] == 'action', 'Unknown authorization type')
        args = item['decision']
        allowed = {'kind', 'target', 'scope_authorized', 'already_authorized', 'platform_permitted',
                   'prerequisites_met', 'authorization', 'platform_approval', 'publication'}
        require(isinstance(args, dict) and {'kind', 'target', 'scope_authorized'} <= set(args) <= allowed,
                'Invalid action authorization facts')
        require(args['kind'] != 'merge', 'Merge drafts require the bound candidate_merge records')
        report = decide_action(**args, now=now)
        expiry = item['expires_at']
        phrase = report['next_step']['authorization']['phrase']
        require(phrase is None or 'AWF1.2' not in phrase, 'Candidate protocol text requires candidate_merge validation')
    _text(item['id'], 'Authorization ID')
    require(timestamp(now) < timestamp(expiry), 'Authorization draft expired; refresh the actual proposal')
    require(report.get('prompt_user', report['next_step']['authorization']['required']) is True
            and report['next_step']['authorization']['required'],
            'Routine or already authorized work must continue without an authorization entry')
    auth = report['next_step']['authorization']
    draft = None if auth['phrase'] is None else {
        'authorization_id': item['id'], 'text': auth['phrase'], 'expires_at': expiry,
        'submit': False, 'execution_authority': False, 'requires_current_source_revalidation': True,
        'delivery': 'UNSENT', 'fallback': 'Show the exact text in chat for the user to copy into the required authorization channel.'}
    return {'id': item['id'], 'report': report, 'input_draft': draft}


def project_cycle(snapshot, contracts, now):
    """Aggregate all work; one blocked stream never stops another ready stream."""
    fields(snapshot, 'schema_version source project observed_at evidence scope_complete cancelled streams pull_requests authorizations wakeup', 'Project cycle')
    require(type(snapshot['schema_version']) is int and snapshot['schema_version'] == 1, 'Unknown project cycle schema')
    require(snapshot['source'] in {'synthetic_fixture', 'host_observation'}, 'Unknown observation source')
    _text(snapshot['project'], 'Project')
    require(all(type(snapshot[k]) is bool for k in ['scope_complete', 'cancelled']), 'Project completion/cancellation facts must be Boolean')
    fresh(snapshot['observed_at'], now, 300, skew_seconds=30)
    require(isinstance(snapshot['evidence'], list) and snapshot['evidence'], 'Current project observation evidence is required')
    for value in snapshot['evidence']:
        _text(value, 'Observation evidence')
    for name, maximum in [('streams', 3), ('pull_requests', 500), ('authorizations', 100)]:
        require(isinstance(snapshot[name], list) and len(snapshot[name]) <= maximum, f'Invalid {name} inventory')
    auth = []
    for item in snapshot['authorizations']:
        require(isinstance(item, dict) and 'id' in item, 'Authorization entry needs a stable ID')
        _text(item['id'], 'Authorization ID')
        try:
            auth.append(_authorization(item, contracts, now))
        except Exception as exc:
            # A stale approval never blocks unrelated implementation or turns
            # into a reusable phrase. The coordinator must refresh this record.
            auth.append({'id': item['id'], 'input_draft': None, 'refresh_required': True,
                         'report': status_report('BLOCKED', 'This decision needs reconciliation: ' + str(exc)[:1500],
                                                 'Refresh the actual proposal, current candidate and authorization records; continue unaffected streams.',
                                                 owner='host coordinator', trigger='now; before presenting any approval text')})
    ids = [item['id'] for item in auth]
    require(len(ids) == len(set(ids)), 'Duplicate authorization IDs')
    pending = set(ids)
    fields(snapshot['wakeup'], 'kind reference evidence', 'Wakeup')
    wake = snapshot['wakeup']
    require(wake['kind'] in {'foreground', 'heartbeat', 'unavailable'}, 'Unknown wakeup kind')
    _text(wake['evidence'], 'Wakeup evidence')
    if wake['kind'] == 'heartbeat':
        _text(wake['reference'], 'Actual registered heartbeat ID')
    else:
        require(wake['reference'] is None, 'Only a registered heartbeat has a schedule reference')
    actions = [{'kind': 'authorization_reconciliation', 'reference': item['id'],
                'action': item['report']['next_step']['action'], 'owner': 'host coordinator',
                'trigger': 'now', 'authorization_id': None} for item in auth if item.get('refresh_required')]
    watches, barriers, labels, urls = [], [], set(), set()
    units = []
    for category, collection, states, ready, running in [
        ('stream', snapshot['streams'], {'READY', 'RUNNING', 'WAITING', 'BLOCKED', 'COMPLETE'}, {'READY'}, {'RUNNING', 'WAITING'}),
        ('pull_request', snapshot['pull_requests'], {'REVIEW_REQUIRED', 'CHANGES_REQUESTED', 'REVIEW_RUNNING', 'CI_RUNNING', 'OWNER_ACTION', 'COMPLETE'}, {'REVIEW_REQUIRED', 'CHANGES_REQUESTED'}, {'REVIEW_RUNNING', 'CI_RUNNING'}),
    ]:
        for item in collection:
            common = 'state summary next_action owner resume_trigger authorization_id'
            fields(item, common + (' label agent_id' if category == 'stream' else ' url stream'), category)
            require(item['state'] in states, f'Invalid {category} state')
            for key in ['summary', 'next_action', 'owner', 'resume_trigger']:
                _text(item[key], key)
            if category == 'stream':
                reference = item['label']
                require(reference in {'A', 'B', 'C'} and reference not in labels, 'Stream labels must be unique A/B/C')
                labels.add(reference)
                if item['agent_id'] is not None:
                    _text(item['agent_id'], 'Actual agent ID')
                require(item['state'] != 'RUNNING' or item['agent_id'] is not None, 'Running stream requires its accepted native agent ID')
            else:
                reference = item['url']
                _text(reference, 'PR URL')
                parsed = urlsplit(reference)
                require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
                        and reference not in urls, 'PR URL must be unique HTTPS without credentials')
                urls.add(reference)
                require(item['stream'] in labels, 'PR must reference an inventoried stream')
            authorization_id = item['authorization_id']
            require(authorization_id is None or authorization_id in pending, 'Work references an unknown required authorization')
            require(authorization_id is None or item['state'] not in ready | running | {'COMPLETE'},
                    'Authorized-to-run or completed work cannot simultaneously require authorization')
            action = {'kind': category, 'reference': reference, 'action': item['next_action'],
                      'owner': item['owner'], 'trigger': item['resume_trigger'], 'authorization_id': authorization_id}
            units.append(item)
            if item['state'] in ready:
                actions.append(action)
            elif item['state'] in running:
                watches.append(action)
            elif item['state'] != 'COMPLETE':
                barriers.append(action)
    active_ids = [s['agent_id'] for s in snapshot['streams'] if s['agent_id'] is not None and s['state'] != 'COMPLETE']
    require(len(active_ids) == len(set(active_ids)), 'One native agent cannot be counted as two concurrent stream writers')
    unfinished = any(unit['state'] != 'COMPLETE' for unit in units) or bool(auth)
    require(not snapshot['scope_complete'] or not unfinished, 'Project completion contradicts pending work, PRs or authorization')
    cancelled = snapshot['cancelled']
    complete = snapshot['scope_complete'] and not unfinished
    state = 'CANCELLED' if cancelled else 'COMPLETE' if complete else 'RUNNING' if actions or any(s['state'] == 'RUNNING' for s in snapshot['streams']) else 'WAITING' if watches or auth else 'BLOCKED'
    if not complete and not cancelled and not actions and not watches and not barriers and not auth:
        actions.append({'kind': 'reconciliation', 'reference': snapshot['project'],
                        'action': 'Reconcile the full authorized backlog and delivery evidence, then select the next eligible work; an empty queue is not project completion.',
                        'owner': 'host coordinator', 'trigger': 'now', 'authorization_id': None})
        state = 'RUNNING'
    if cancelled:
        actions, watches = [], []
    action = ('Respect cancellation, reconcile surviving workers and preserve the project handoff.' if cancelled else
              'Report the completed authorized scope and final delivery evidence; no in-scope work remains.' if complete else
              'Publish this progress update, perform all ready actions, and observe active work. Reconcile completions and repeat this cycle without waiting for a user continue message.' if actions or watches else
              'Report each real blocker and prepared authorization, keep the project resumable, and resume affected work when its named trigger occurs.')
    result = {'status': state, 'project': snapshot['project'], 'snapshot_hash': fingerprint('project-cycle', snapshot),
              'execution_authority': False, 'synthetic': snapshot['source'] == 'synthetic_fixture', 'finish_allowed': complete or cancelled,
              'status_update_ends_work': False, 'continue_actions': actions, 'watch_actions': watches,
              'barriers': barriers, 'streams': deepcopy(snapshot['streams']), 'pull_requests': deepcopy(snapshot['pull_requests']),
              'prs_requiring_review': [deepcopy(p) for p in snapshot['pull_requests'] if p['state'] in {'REVIEW_REQUIRED', 'CHANGES_REQUESTED', 'OWNER_ACTION'}],
              'pending_authorizations': [] if cancelled else auth,
              'suspended_authorization_ids': ids if cancelled else [],
              'input_drafts': [] if cancelled else [x['input_draft'] for x in auth if x['input_draft']],
              'wakeup': deepcopy(wake), 'reported_heartbeat_registered': wake['kind'] == 'heartbeat',
              'background_execution_verified_by_this_tool': False,
              'next_step': next_step(action, owner='host coordinator', trigger='now; complete the terminal handoff' if complete or cancelled else 'now' if actions else 'the named worker, CI, dependency or owner event', continue_independent_work=not (complete or cancelled))}
    if complete or cancelled:
        result['continuity_requirement'] = ('No further in-scope execution remains. Retain delivery evidence and disable only this project objective\'s registered wakeup.' if complete else
                                           'Do not start further project work or present suspended approvals. Reconcile surviving workers and disable only this cancelled objective\'s registered wakeup; preserve user-authored input and history.')
    elif wake['kind'] == 'unavailable':
        result['continuity_requirement'] = 'Keep the foreground coordinator active. If the host must end the turn/session, use a supported resumable task mechanism and verify its registration; otherwise report that automatic wakeup is unavailable and name the manual resume trigger.'
    else:
        result['continuity_requirement'] = 'Continue the foreground cycle or use the recorded registered heartbeat; never create duplicate project writers.'
    return result


def deliver_input_draft(draft, now, composer=None):
    """Optional host adapter: read_draft, compare_and_set_draft, readback; no send.

The caller must revalidate the current proposal and channel before delivery.
A host without this real adapter receives copyable text, not a prefill claim.
"""
    require(isinstance(draft, dict) and draft.get('submit') is False and draft.get('execution_authority') is False,
            'Only an unsent, non-authorizing draft can be delivered')
    _text(draft.get('text'), 'Exact draft text')
    require(timestamp(now) < timestamp(draft['expires_at']), 'Do not prefill an expired authorization')
    fallback = {'status': 'COPYABLE_TEXT', 'text': draft['text'], 'submitted': False, 'prefilled': False}
    if composer is None:
        return {**fallback, 'reason': 'This host exposes no input-draft adapter.'}
    try:
        current = composer.read_draft()
        if current['text'] == draft['text']:
            return {**fallback, 'status': 'PREFILLED', 'prefilled': True}
        if current['text']:
            return {**fallback, 'reason': 'Existing user input is preserved; copy or insert the prepared text manually.'}
        if not composer.compare_and_set_draft(current['revision'], draft['text']):
            return {**fallback, 'reason': 'Input changed concurrently; preserve it and show the copyable text.'}
        if composer.read_draft()['text'] != draft['text']:
            return {**fallback, 'reason': 'Prefill readback did not match; report the uncertain draft outcome.'}
        return {**fallback, 'status': 'PREFILLED', 'prefilled': True}
    except Exception:
        return {**fallback, 'reason': 'Host draft operation failed or is uncertain; inspect the input and use the copyable text.'}


def render_cycle(value):
    def escaped(text):
        return str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('|', '\\|').replace('`', '\\`').replace('[', '\\[').replace(']', '\\]')
    lines = [f"State: {value['status']} — {escaped(value['project'])}",
             'Synthetic demonstration only; no live dispatch or approval.' if value['synthetic'] else 'Based on the coordinator\'s current observation; revalidate source state before acting.', '',
             '| Stream | State | Agent | Current status | Next action / owner / trigger |',
             '| --- | --- | --- | --- | --- |']
    for item in value['streams']:
        lines.append('| ' + ' | '.join(escaped(v) for v in [item['label'], item['state'], item['agent_id'] or 'Unassigned', item['summary'],
                     f"{item['next_action']} / {item['owner']} / {item['resume_trigger']}"]) + ' |')
    lines += ['', 'PR status:']
    for item in value['pull_requests']:
        lines.append(f"- {escaped(item['url'])}: {item['state']} — {escaped(item['summary'])}. Next: {escaped(item['next_action'])}; owner: {escaped(item['owner'])}; trigger: {escaped(item['resume_trigger'])}.")
    if not value['pull_requests']:
        lines.append('No PRs recorded in this verified project snapshot.')
    from .interaction import render_markdown
    for item in value['pending_authorizations']:
        lines += ['', f"Required decision: {escaped(item['id'])}", render_markdown(item['report']).rstrip()]
    if not value['pending_authorizations']:
        lines += ['', 'Required authorization: none.']
    lines += ['', 'Next: ' + value['next_step']['action'], 'Owner: ' + value['next_step']['owner'] + '.',
              'Trigger: ' + value['next_step']['trigger'] + '.', value['continuity_requirement'],
              'This report does not submit authorizations, launch agents or register a scheduler.']
    return '\n'.join(lines) + '\n'
