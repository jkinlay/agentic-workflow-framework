"""Pure default-OFF reporting normalization and field allowlists; no authentication."""
from copy import deepcopy
import re

from .delivery import require, validate_binding

MODES = ('off', 'teams', 'slack', 'both')

# Settings are identifiers, never arbitrary labels or credential containers.
# Keep these shapes aligned with both operating schemas. Adapter-only bindings
# retain their separate host contract; this is the owner-facing settings gate.
_SLUG = r'[a-z][a-z0-9]{0,15}(?:-[a-z0-9]{1,16}){0,3}'
_UUID = r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}'
BINDING_PATTERNS = {
    'project': r'(?:' + _UUID + '|' + _SLUG + ')',
    'repository': r'(?:[1-9][0-9]{0,19}|' + _SLUG + ')',
    'scope_revision': r'(?:[0-9a-f]{64}|' + _SLUG + ')',
    'revision': r'(?:[0-9]{1,10}|binding-[0-9]{1,10})',
    'destination': r'(?:[CG][A-Z0-9]{1,79}|' + _UUID +
                   r'/19:[A-Za-z0-9_.-]{1,128}@thread\.(?:tacv2|skype)|' + _SLUG + ')',
    'secret_ref': r'(?:env:[A-Za-z_][A-Za-z0-9_]{0,127}|(?:vault|secret):'
                  r'[A-Za-z][A-Za-z0-9_-]{0,31}(?:/[A-Za-z][A-Za-z0-9_-]{0,31}){0,3})',
}


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
        if binding is not None:
            require(all(item[k] == binding[k] for k in binding), 'REPORT_SCOPE_BINDING_MISMATCH')
        for field, pattern in BINDING_PATTERNS.items():
            require(re.fullmatch(pattern, item[field]) is not None,
                    'REPORT_BINDING_SHAPE_INVALID: ' + field)
        require(item['platform'] not in seen, 'REPORT_BINDING_DUPLICATE')
        seen.add(item['platform'])
        scopes.add(tuple(item[k] for k in ('project', 'repository', 'scope_revision')))
    require(len(scopes) <= 1, 'REPORT_SCOPE_BINDING_MISMATCH')
    selected = {'teams', 'slack'} if result['mode'] == 'both' else {result['mode']} - {'off'}
    require(selected <= seen, 'REPORT_BINDING_REQUIRED')
    return result
