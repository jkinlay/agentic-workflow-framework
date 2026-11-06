"""Default-OFF reporting choices; trusted host authenticates destination approval."""
from copy import deepcopy
import re

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


def validate_reporting_change(current, changes, effective):
    """Pure shared-admission validator; collect no identity, secrets or state."""
    require(isinstance(changes, dict) and bool(changes) and set(changes) <= {'mode', 'bindings'},
            'REPORT_SETTINGS_INVALID')
    prior = effective_reporting(current)
    result = effective_reporting({'reporting': {**prior, **deepcopy(changes)}})
    require(result == effective_reporting(effective), 'REPORT_SETTINGS_TRANSITION_MISMATCH')
