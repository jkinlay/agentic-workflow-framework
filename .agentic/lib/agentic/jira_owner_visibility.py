"""Non-authoritative Jira shared-account reporting, independent of admission.

Read accepted default-branch bytes separately so a working proposal cannot hide
the accepted warning. No report returned here enables a closure or an adapter.
"""
from pathlib import Path

from . import ValidationError
from .canonical import load, load_yaml
from .shared_owner_policy import JIRA_SETTING, shared_owner_warnings

CODE = 'SHARED_OWNER_CONTROLLER_JIRA_ACCOUNT'


def jira_owner_warnings(root=None, config=None, *, config_path=None):
    """Report accepted and proposed accounts without consuming owner authority."""
    warnings = []
    # An external supplied file has no accepted-policy root association. Report
    # its proposal without probing an unrelated default CLI repository.
    if root is not None and (config_path is None or
            Path(config_path).resolve() == (Path(root) / '.agentic/PROJECT_CONFIG.yaml').resolve()):
        root = Path(root).resolve()
        try:
            from . import shared_owner_policy as policy
            from .contracts import Contracts
            from .policy import policy_hash
            # Reporting reads independently; observation enforces the shared
            # whole-repository rule before describing accepted authority.
            ref, _ = policy._default_ref(root)
            if ref is not None:
                commit = policy._git(root, 'rev-parse', '--verify', ref + '^{commit}').decode('ascii').strip()
                raw = policy._policy_blob(root, commit, '.agentic/PROJECT_CONFIG.yaml')
                if raw is not None:
                    accepted = load_yaml(raw)
                    # Absence in immutable default-branch bytes needs no
                    # authority observation. Working bytes cannot hide a
                    # non-null accepted setting; that path still rechecks.
                    if any(item['code'] == CODE for item in shared_owner_warnings(accepted)):
                        workflow_raw = policy._policy_blob(root, commit, '.agentic/workflow.yaml')
                        if workflow_raw is not None:
                            workflow = load_yaml(workflow_raw)
                            observation = policy.observe_accepted_owner_policy(root, accepted, workflow,
                                Contracts(Path(__file__).resolve().parents[2] / 'schemas'),
                                policy_sha256=policy_hash(accepted, workflow))
                            for item in observation['warnings']:
                                if item['code'] == CODE:
                                    warnings.append({**item, 'message': item['message'] +
                                        ' Accepted default-branch policy ' + observation['policy_sha256'] +
                                        '; this report is not authority and does not enable the opt-out.'})
        except (ValidationError, OSError, ValueError, KeyError, TypeError, UnicodeError):
            # Reporting must never turn an unavailable observation into admission
            # or block an otherwise independent command.
            pass
        if config is None:
            try:
                config = load(root / '.agentic/PROJECT_CONFIG.yaml')
            except (ValidationError, OSError, ValueError, UnicodeError):
                config = None
    if config_path is not None:
        try:
            config = load(config_path)
        except (ValidationError, OSError, ValueError, UnicodeError):
            config = None
    for item in shared_owner_warnings(config):
        if item['code'] == CODE:
            warnings.append({**item, 'message': item['message'] +
                ' Supplied or working-tree configuration is not authority and does not enable the opt-out.'})
    return warnings


def warning_lines(warnings):
    return ['WARNING: ' + item['code'] + ': ' + item['message'] for item in warnings]


def retained_jira_warnings(value):
    """Describe retained annotations; never interpret a provider body as policy."""
    result = []
    if isinstance(value, dict):
        policy = value.get('accepted_owner_policy')
        if (value.get('shared_owner_controller_account') is True and isinstance(policy, dict)
                and value.get('owner_policy_sha256') == policy.get('policy_sha256')):
            for item in shared_owner_warnings({'jira': {JIRA_SETTING: policy.get(JIRA_SETTING)}}):
                result.append({**item, 'message': item['message'] +
                    ' Retained closure policy ' + str(policy['policy_sha256']) +
                    '; this digest is not authority and does not enable the opt-out.'})
        for key, child in value.items():
            if key != 'source':
                result.extend(retained_jira_warnings(child))
    elif isinstance(value, (tuple, list)):
        for child in value:
            result.extend(retained_jira_warnings(child))
    return result


def with_jira_owner_warnings(output, warnings):
    """Preserve every byte/field of the unset-setting result."""
    if not warnings:
        return output
    output = dict(output)
    existing = output.get('warnings', [])
    # Inspection already reports proposed accounts. Keep that existing message
    # and add explicit source/authority context, rather than silently replacing it.
    combined = list(existing)
    for item in warnings:
        if item not in combined:
            combined.append(item)
    output['warnings'] = combined
    return output
