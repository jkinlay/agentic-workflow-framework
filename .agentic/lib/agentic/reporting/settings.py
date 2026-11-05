"""Default-OFF reporting choices; trusted host authenticates destination approval."""
from copy import deepcopy
import re

from ..canonical import fingerprint
from ..budget_enrollment import EnrollmentRegistry, encoded, text_id
from .delivery import require, validate_binding

MODES = ('off', 'teams', 'slack', 'both')


def effective_reporting(root, *, binding=None):
    require(isinstance(root, dict), 'REPORT_SETTINGS_REQUIRED')
    value = root.get('reporting', {})
    require(isinstance(value, dict) and set(value) <= {'mode', 'bindings'}, 'REPORT_SETTINGS_INVALID')
    result = dict(mode=value.get('mode', 'off'), bindings=deepcopy(value.get('bindings', [])))
    require(result['mode'] in MODES, 'REPORT_MODE_INVALID')
    require(isinstance(result['bindings'], list) and len(result['bindings']) <= 2, 'REPORT_BINDINGS_INVALID')
    seen, scopes = set(), set()
    for item in result['bindings']:
        validate_binding(item)
        require(item['platform'] not in seen, 'REPORT_BINDING_DUPLICATE')
        seen.add(item['platform'])
        scopes.add(tuple(item[k] for k in ('project', 'repository', 'scope_revision')))
        require(re.fullmatch(r'(?:env|vault|secret):[A-Za-z0-9_./-]+', item['secret_ref']) is not None,
                'REPORT_SECRET_REFERENCE_REQUIRED')
        if binding is not None:
            require(all(item[k] == binding[k] for k in binding), 'REPORT_SCOPE_BINDING_MISMATCH')
    require(len(scopes) <= 1, 'REPORT_SCOPE_BINDING_MISMATCH')
    selected = {'teams', 'slack'} if result['mode'] == 'both' else {result['mode']} - {'off'}
    require(selected <= seen, 'REPORT_BINDING_REQUIRED')
    return result


class ReportingSettingsRegistry(EnrollmentRegistry):
    """Reuse protected host admission, with separate immutable replay identities."""
    def admit_owner_direction(self, project_id, current, changes):
        require(text_id(project_id), 'REPORT_PROJECT_REQUIRED')
        require(isinstance(changes, dict) and bool(changes) and set(changes) <= {'mode', 'bindings'},
                'REPORT_SETTINGS_INVALID')
        prior = effective_reporting({'reporting': current})
        result = effective_reporting({'reporting': {**prior, **deepcopy(changes)}})
        require(self.host is not None and callable(getattr(self.host, 'observe_owner_direction', None)),
                'Trusted-host owner observer unavailable')
        prior_hash = fingerprint('reporting-settings', prior)
        observed = self.host.observe_owner_direction(project_id, prior_hash, deepcopy(changes))
        require(isinstance(observed, dict) and set(observed) == {
            'observation_id', 'actor_id', 'project_id', 'prior_settings_hash', 'changes'},
            'REPORT_OWNER_OBSERVATION_INVALID')
        require(text_id(observed['observation_id']) and text_id(observed['actor_id']) and
                observed['project_id'] == project_id and observed['prior_settings_hash'] == prior_hash and
                encoded(observed['changes']) == encoded(changes), 'REPORT_OWNER_OBSERVATION_MISMATCH')
        with self.transaction('CREATE TABLE IF NOT EXISTS reporting_owner_observations '
            '(observation_id TEXT PRIMARY KEY, observation TEXT NOT NULL);'
            'CREATE TRIGGER IF NOT EXISTS reporting_observations_no_update '
            'BEFORE UPDATE ON reporting_owner_observations '
            "BEGIN SELECT RAISE(ABORT,'immutable owner observation'); END;"
            'CREATE TRIGGER IF NOT EXISTS reporting_observations_no_delete '
            'BEFORE DELETE ON reporting_owner_observations '
            "BEGIN SELECT RAISE(ABORT,'immutable owner observation'); END;") as db:
            require(db.execute('SELECT 1 FROM reporting_owner_observations WHERE observation_id=?',
                (observed['observation_id'],)).fetchone() is None, 'REPORT_OWNER_OBSERVATION_REPLAY')
            db.execute('INSERT INTO reporting_owner_observations VALUES(?,?)',
                       (observed['observation_id'], encoded(observed)))
        return dict(settings=result, observation=deepcopy(observed))
