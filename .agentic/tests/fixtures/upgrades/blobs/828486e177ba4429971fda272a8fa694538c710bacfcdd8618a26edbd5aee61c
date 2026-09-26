"""Durable, bounded review cycle. No merge, Jira mutation or remote reporting."""
from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import uuid

from . import ValidationError
from .canonical import now_text, timestamp, sha256
from .interaction import loop_next_step, render_markdown
from .safeio import Tree

BOUNDARY_CODES = ('PROTECTED_PATH', 'SCOPE_ESCAPE', 'CREDENTIAL_EXPOSURE', 'UNSAFE_PATH', 'UNREGISTERED_PRODUCER', 'CI_BINDING', 'INDEPENDENCE')
CAP_REASON = 'REVIEW_CAP_REACHED'
PARKED_REASON = 'REVIEW_CAP_PARKED'
CAP_DECISIONS = ('MERGE_WITH_NOTES', 'PARK', 'RESCOPE', 'EXTEND_ONE_CYCLE')
HARD_MAX_CAP_EXTENSIONS = 3


def cap_limit(config, state):
    """Governance cap plus recorded extensions; extensions are finite by construction."""
    return config['max_amendment_cycles'] + state.get('cap_extensions', 0)


def valid_basis(value):
    if not isinstance(value, str):
        return False
    kind, _, key = value.partition(':')
    return bool(key.strip()) and (kind == 'criterion' or (kind == 'boundary' and key in BOUNDARY_CODES))


def is_boundary(finding):
    return str(finding.get('basis', '')).startswith('boundary:')


def serious(finding):
    """BLOCKER/MAJOR, or any boundary-basis finding whatever its severity."""
    return finding['severity'] in {'BLOCKER', 'MAJOR'} or is_boundary(finding)


def evidence_only(paths, patterns):
    import fnmatch
    return bool(paths) and bool(patterns) and all(any(fnmatch.fnmatchcase(p, q) for q in patterns) for p in paths)


def require(condition, message):
    if not condition:
        raise ValidationError(message)


class LoopStore:
    """One canonical host database; transaction lock spans each bounded tick.

    Inflight intent is committed before external execution. An abandoned intent
    pauses on restart, even after the OS releases its lock. Never replay writes.
    """
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'review-loop.sqlite3', timeout=0, isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS prs (key TEXT PRIMARY KEY, owner TEXT UNIQUE NOT NULL, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, key TEXT NOT NULL, at TEXT NOT NULL, state TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()

    @contextmanager
    def lock(self):
        # A separate connection holds the process-wide coordination lock while
        # state transactions commit. No lease expiry can create a second writer.
        lock = sqlite3.connect(self.directory / 'writer-lock.sqlite3', timeout=0, isolation_level=None)
        try:
            lock.execute('BEGIN EXCLUSIVE')
            yield
        except sqlite3.OperationalError as exc:
            raise ValidationError('Host review loop is busy; retry a later tick') from exc
        finally:
            lock.close()

    def get(self, key):
        row = self.db.execute('SELECT state FROM prs WHERE key=?', (key,)).fetchone()
        require(row is not None, 'PR is not enrolled')
        state=json.loads(row[0])
        # Enrollments recorded by 1.8.9 lack the cap fields; defaults never reset consumed cycles.
        for name, value in (('cap_extensions', 0), ('evidence_only_amendments', 0), ('dispositions', [])):
            state.setdefault(name, value)
        state['next_step']=loop_next_step(state)
        return state

    def save(self, state, new=False):
        state['next_step'] = loop_next_step(state)
        relative='reports/' + sha256(state['key'].encode('utf-8')) + '.md'
        state['status_report_path']=str(self.directory / relative)
        state['reported_at']=now_text()
        body = json.dumps(state, sort_keys=True)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if new:
                self.db.execute('INSERT INTO prs VALUES (?,?,?)', (state['key'], state['owner'], body))
            else:
                self.db.execute('UPDATE prs SET state=? WHERE key=?', (body, state['key']))
            self.db.execute('INSERT INTO events(key,at,state) VALUES (?,?,?)', (state['key'], now_text(), body))
            self.db.execute('COMMIT')
        except Exception:
            self.db.execute('ROLLBACK')
            raise
        with Tree(self.directory) as tree:
            tree.write(relative, (f'Recorded at: {state["reported_at"]}\n\n'+render_markdown(state)).encode('utf-8'))


def enroll(store, config, snapshot):
    initial = deepcopy(config.get('initial_findings', []))
    validate_review({'candidate':snapshot,'verdict':'BLOCKED','reviewed_files':[],
        'findings':initial,'summary':'Operator-adopted initial finding ledger'}, snapshot, [], [])
    state = {'key': config['key'], 'owner': config['repository'] + ':' + snapshot['head_ref'],
        'config_hash': config['config_hash'], 'phase': 'REVIEW', 'candidate': snapshot,
        'findings': initial, 'cycles': 0, 'cap_extensions': 0, 'evidence_only_amendments': 0, 'dispositions': [],
        'agent_runs': 0, 'wait_ticks': 0, 'generation': 1,
        'inflight': None, 'last_review': None, 'reason': 'Enrolled', 'history': []}
    with store.lock():
        store.save(state, new=True)
    return state


def pause(store, key, reason):
    with store.lock():
        state = store.get(key)
        state.update(phase='PAUSED', reason=reason, generation=state['generation'] + 1)
        store.save(state)
        return state


def resume(store, config, snapshot, reconciled_run, disposition=None):
    """Resume a paused enrollment. A cap pause resumes only through an owner disposition record."""
    with store.lock():
        state = store.get(config['key'])
        require(state['phase'] == 'PAUSED', 'Only paused enrollments can resume')
        require(state['config_hash'] == config['config_hash'], 'Configuration changed; retire and enroll a new reviewed configuration')
        expected = state['inflight']['id'] if state['inflight'] else 'none'
        require(reconciled_run == expected, 'Inspect and reconcile the inflight run before resuming')
        at_cap = state['cycles'] >= cap_limit(config, state)
        if disposition is None:
            require(not at_cap and not str(state.get('reason', '')).startswith((CAP_REASON, PARKED_REASON)),
                    'Amendment budget exhausted; resume cannot reset it. Record an owner cap disposition (MERGE_WITH_NOTES, PARK, RESCOPE or EXTEND_ONE_CYCLE)')
            state.update(phase='REVIEW', candidate=snapshot, inflight=None, last_review=None,
                         wait_ticks=0, reason='Operator reconciled and resumed', generation=state['generation'] + 1)
            store.save(state)
            return state
        return apply_cap_disposition(store, config, state, snapshot, disposition)


def apply_cap_disposition(store, config, state, snapshot, disposition):
    """Owner-signed cap disposition; the loop never produces one itself."""
    require(isinstance(disposition, dict) and set(disposition) >= {'decision', 'disposition_id', 'owner', 'open_finding_ids'}, 'Invalid cap disposition record')
    require(disposition['decision'] in CAP_DECISIONS, 'Unknown cap disposition decision')
    operator = (config.get('qualification') or {}).get('operator')
    require(isinstance(disposition['owner'], str) and disposition['owner'].strip() and 'agent' not in disposition['owner'].casefold()
            and (operator is None or disposition['owner'] == operator), 'Cap dispositions are decisions of the enrolled qualification operator')
    require(state['cycles'] >= cap_limit(config, state) or str(state.get('reason', '')).startswith((CAP_REASON, PARKED_REASON)), 'No amendment cap has been reached')
    open_ids = sorted(x['id'] for x in state['findings'] if x['status'] != 'RESOLVED' and serious(x))
    require(sorted(disposition['open_finding_ids']) == open_ids, f'Disposition must list the open serious findings exactly: {open_ids}')
    require(disposition['disposition_id'] not in {d['disposition_id'] for d in state['dispositions']}, 'Disposition already applied')
    decision = disposition['decision']
    limit = min(config.get('max_cap_extensions', 2), HARD_MAX_CAP_EXTENSIONS)
    state['dispositions'].append({k: disposition[k] for k in ('decision', 'disposition_id', 'owner', 'open_finding_ids')})
    if decision == 'EXTEND_ONE_CYCLE':
        require(state['cap_extensions'] < limit,
                f'Extension {state["cap_extensions"] + 1} exceeds max_cap_extensions = {limit} ($.execution.max_cap_extensions); choose MERGE_WITH_NOTES, PARK or RESCOPE')
        state['cap_extensions'] += 1
        state.update(phase='AMEND', candidate=snapshot, inflight=None, last_review=None, wait_ticks=0,
                     reason=f'Owner extended the amendment cap once ({state["cap_extensions"]} of {limit})', generation=state['generation'] + 1)
    elif decision == 'MERGE_WITH_NOTES':
        boundaries = [x['id'] for x in state['findings'] if x['id'] in open_ids and is_boundary(x)]
        require(not boundaries, 'MERGE_WITH_NOTES cannot carry open boundary findings: ' + ', '.join(boundaries))
        state['residual_notes'] = [f"{x['id']} {x['severity']} {x['file']}: {x['message']}" for x in state['findings'] if x['id'] in open_ids]
        state.update(phase='WAIT_CI', candidate=snapshot, inflight=None, wait_ticks=0,
                     reason='Owner chose merge with notes; open findings carried as residual risks to the final gate and merge authorization', generation=state['generation'] + 1)
    elif decision == 'PARK':
        state.update(phase='PAUSED', reason=f'{PARKED_REASON}: owner parked the enrollment; resume needs a further disposition', generation=state['generation'] + 1)
    else:
        require(isinstance(disposition.get('successor_ticket'), str) and disposition['successor_ticket'].strip(), 'RESCOPE needs a successor ticket')
        state.update(phase='CLOSED', reason=f"RESCOPED: superseded by {disposition['successor_ticket']}; no merge performed", generation=state['generation'] + 1)
    store.save(state)
    return state


def validate_review(report, candidate, prior, files):
    require(isinstance(report, dict) and set(report) == {'candidate','verdict','reviewed_files','findings','summary'}, 'Invalid critic record')
    require(report['candidate'] == candidate, 'Critic candidate is stale or incomplete')
    require(report['verdict'] in {'APPROVE','CHANGES_REQUESTED','BLOCKED'}, 'Unknown critic verdict')
    require(isinstance(report['summary'], str) and report['summary'].strip(), 'Critic summary is empty')
    require(isinstance(report['reviewed_files'], list) and len(report['reviewed_files']) == len(set(report['reviewed_files'])) and set(report['reviewed_files']) == set(files), 'Critic did not cover every changed file')
    require(isinstance(report['findings'], list), 'Invalid finding ledger')
    old = {x['id']: x for x in prior}
    seen = set()
    loci = {}
    for item in prior:
        if valid_basis(item.get('basis', '')):
            loci.setdefault((item['file'], item['basis']), []).append(item['id'])
    for item in report['findings']:
        require(isinstance(item, dict) and set(item) - {'basis', 'supersedes'} == {'id','severity','status','file','message','evidence'}, 'Invalid finding')
        require(all(isinstance(item[k], str) for k in item), 'Finding fields must be strings')
        require(item['id'].strip() and item['id'] not in seen and item['message'].strip(), 'Empty or duplicate finding identity/message')
        seen.add(item['id'])
        require(item['severity'] in {'BLOCKER','MAJOR','MINOR'} and item['status'] in {'OPEN','DISPUTED','RESOLVED'}, 'Unknown finding severity/status')
        if item['severity'] in {'BLOCKER', 'MAJOR'}:
            # Block only on an acceptance criterion or a mandatory boundary; the
            # record is refused so the critic re-emits, never silently downgraded.
            require(valid_basis(item.get('basis')), f"Finding {item['id']}: BLOCKER/MAJOR needs basis 'criterion:<id>' or 'boundary:<code>'")
        previous = old.get(item['id'])
        if previous:
            require(item['severity'] == previous['severity'] and item['file'] == previous['file'] and item['message'] == previous['message'], 'Finding identity/severity changed; retain the original lineage')
            require(item.get('basis', previous.get('basis')) == previous.get('basis'), 'Finding basis changed; retain the original lineage')
        elif item['severity'] in {'BLOCKER', 'MAJOR'}:
            lineage = loci.get((item['file'], item['basis']), [])
            require(not lineage or item.get('supersedes') in lineage,
                    f"Finding {item['id']}: locus already has {lineage}; name the superseded finding")
            require(not item.get('supersedes') or item.get('supersedes') in old, 'supersedes names an unknown prior finding')
        require(item['status'] != 'RESOLVED' or item['evidence'].strip(), 'Resolution requires critic evidence')
    require(set(old) <= seen, 'Prior findings were dropped')
    blockers = any(serious(x) and x['status'] != 'RESOLVED' for x in report['findings'])
    require(report['verdict'] != 'APPROVE' or not blockers, 'Critic approved unresolved blocking findings')
    require(report['verdict'] != 'CHANGES_REQUESTED' or any(x['status'] != 'RESOLVED' for x in report['findings']), 'Changes requested without actionable findings')
    return deepcopy(report['findings'])


def tick(store, config, driver):
    """One review, amendment or CI observation per invocation; scheduler repeats.

    The host adapter validates raw observations. Synthetic adapters are test seams,
    not selectable by the production CLI. Every side effect has durable intent.
    """
    with store.lock():
        state = store.get(config['key'])
        if state['phase'] in {'PAUSED','CLOSED'}:
            return state
        if state['inflight']:
            state.update(phase='PAUSED', reason='Uncertain prior run; inspect processes, Git and retained run evidence')
            store.save(state)
            return state
        try:
            require(state['config_hash'] == config['config_hash'], 'Configuration changed since enrollment')
            current = driver.snapshot()
            if current is None:
                state.update(phase='CLOSED', reason='PR is closed; no merge performed')
                store.save(state)
                return state
            if current != state['candidate']:
                # Unsolicited head changes break writer ownership; base-only changes
                # safely invalidate review and re-review the complete new tuple.
                require(current['head'] == state['candidate']['head'] and current['head_ref'] == state['candidate']['head_ref'] and current['base_ref'] == state['candidate']['base_ref'], 'Unexpected writer/head/target change')
                state.update(candidate=current, phase='REVIEW', last_review=None, wait_ticks=0)
            if state['last_review'] and state['phase'] in {'WAIT_CI','READY_FOR_FINAL_GATE'}:
                age = (timestamp(now_text()) - timestamp(state['last_review']['completed_at'])).total_seconds()
                require(age >= 0, 'Host clock moved backwards')
                if age > config.get('max_review_age_seconds', 3600):
                    state.update(phase='REVIEW', last_review=None, wait_ticks=0, reason='Review freshness expired')
            if state['phase'] == 'READY_FOR_FINAL_GATE':
                ci = driver.ci(current)
                require(driver.snapshot() == current, 'PR moved during CI observation')
                if ci != 'PASS':
                    state.update(phase='WAIT_CI', reason='CI readiness invalidated', wait_ticks=0)
                    store.save(state)
                return state
            phase = state['phase']
            if phase == 'WAIT_CI':
                ci = driver.ci(current)
                require(driver.snapshot() == current, 'PR moved during CI observation')
                if ci == 'FAIL':
                    finding = next((x for x in state['findings'] if x['id'] == 'AWF-CI'), None)
                    if finding is None:
                        finding = {'id':'AWF-CI','severity':'MAJOR','status':'OPEN','file':'','basis':'boundary:CI_BINDING',
                            'message':'Required current-head CI failed; inspect the configured GitHub Actions checks and repair within scope.', 'evidence':''}
                        state['findings'].append(finding)
                    finding.update(status='OPEN', evidence='')
                    state.update(phase='AMEND', reason='Required CI failed; bounded automatic amendment requested', last_review=None)
                elif ci == 'PASS':
                    require(state['last_review'] is not None and state['last_review']['candidate'] == current, 'CI has no matching independent review')
                    state.update(phase='READY_FOR_FINAL_GATE', reason='Review and current-head checks passed; final gate and human merge still required')
                else:
                    state['wait_ticks'] += 1
                    require(state['wait_ticks'] <= config['max_ci_wait_ticks'], 'CI wait budget exhausted')
                store.save(state)
                return state
            require(phase in {'REVIEW','AMEND'}, 'Unknown review loop phase')
            require(state['agent_runs'] < config['max_agent_runs'], 'Total agent-run budget exhausted')
            if phase == 'AMEND' and state['cycles'] >= cap_limit(config, state):
                # Cap reached is a state, not a dead end: present the open findings
                # and wait for one owner disposition. No further amendment runs.
                open_ids = sorted(x['id'] for x in state['findings'] if x['status'] != 'RESOLVED' and serious(x))
                state.update(phase='PAUSED', reason=f'{CAP_REASON}: {state["cycles"]} of {cap_limit(config, state)} amendment cycles used; open findings {open_ids}; '
                             f'owner decides MERGE_WITH_NOTES, PARK, RESCOPE or EXTEND_ONE_CYCLE (extensions used {state["cap_extensions"]} of {min(config.get("max_cap_extensions", 2), HARD_MAX_CAP_EXTENSIONS)})')
                store.save(state)
                return state
            if phase == 'AMEND':
                state['cycles'] += 1  # Attempts, including interrupted or failed runs.
            run_id = str(uuid.uuid4())
            state['agent_runs'] += 1
            state['inflight'] = {'id': run_id, 'phase': phase, 'candidate': current, 'started_at': now_text()}
            store.save(state)
            if phase == 'REVIEW':
                files = driver.files(current)
                report = driver.review(current, deepcopy(state['findings']), run_id, files)
                require(driver.snapshot() == current, 'PR moved during review')
                findings = validate_review(report, current, state['findings'], files)
                state['findings'] = findings
                state['last_review'] = {'run_id': run_id, 'candidate': current, 'report': report, 'completed_at': now_text()}
                if report['verdict'] == 'BLOCKED':
                    state.update(phase='PAUSED', reason='Critic blocked: '+report['summary'][:4000])
                elif report['verdict'] == 'CHANGES_REQUESTED':
                    state.update(phase='AMEND', reason='Critic requested changes')
                else:
                    state.update(phase='WAIT_CI', reason='Independent review passed', wait_ticks=0)
            else:
                amended = driver.amend(current, deepcopy(state['findings']), run_id)
                require(amended != current and amended['head'] != current['head'], 'Amendment made no commit progress')
                require(all(amended[k] == current[k] for k in current if k != 'head'), 'Amendment changed base or PR identity')
                require(driver.snapshot() == amended, 'Published amendment is not current')
                paths = getattr(driver, 'last_amendment_paths', None)
                if paths is not None and evidence_only(paths, config.get('evidence_paths', [])):
                    state['cycles'] -= 1  # Evidence/documentation-only amendments do not consume a cycle.
                    state['evidence_only_amendments'] += 1
                state.update(candidate=amended, phase='REVIEW', last_review=None, reason='Amendment published; fresh critic required', wait_ticks=0)
            state['history'].append(state['inflight'])
            state['inflight'] = None
            store.save(state)
        except Exception as exc:
            # Retain intent on any uncertain operation; an operator must reconcile.
            state.update(phase='PAUSED', reason=f'{type(exc).__name__}: {exc}')
            store.save(state)
        return state
