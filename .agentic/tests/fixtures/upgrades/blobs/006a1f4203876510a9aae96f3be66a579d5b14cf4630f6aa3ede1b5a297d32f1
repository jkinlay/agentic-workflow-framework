"""Bound publication classification; all API observations below are synthetic."""
import copy
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from agentic import ValidationError
from agentic.interaction import decide_action as action_decision, publication_classification as classify
from agentic.canonical import load
from agentic.continuation import project_cycle
from agentic.contracts import Contracts
from agentic.repository_rules import TEMPLATE, synthetic_observation

NOW = '2026-09-15T12:00:00Z'
REPO = 'fixture/project'
REF = 'refs/heads/codex/TEST-7-routine-flow'
TARGET = REPO + ':' + REF
ROOT = Path(__file__).resolve().parents[2]
AUTH = {'required': True, 'reason': 'This specific publication is outside the routine branch policy.',
        'source': 'Accepted project branch publication policy',
        'phrase': 'Authorize the prepared publication to the specified repository and ref.'}


def facts():
    detail = copy.deepcopy(TEMPLATE)
    detail.update(id=7, source_type='Repository', source=REPO)
    effective = [{**copy.deepcopy(rule), 'ruleset_id': 7, 'ruleset_source_type': 'Repository',
                  'ruleset_source': REPO} for rule in detail['rules']]
    observation = synthetic_observation(REPO, 'main', repository_id=101, rules=effective,
                                        rulesets=[detail], observed_at=NOW)
    observation['source'] = 'github_api'  # Mocked transport, not real live evidence.
    return {'github': {'host': 'https://github.com', 'repository': REPO, 'repository_id': 101,
                       'base_branch': 'main', 'branch_pattern': 'codex/{ticket}-{slug}'},
            'repository': REPO, 'repository_id': 101, 'ref': REF, 'ticket': 'TEST-7',
            'slug': 'routine-flow', 'force': False, 'delete': False,
            'rules_observation': observation}


def decide_action(*args, **kwargs):
    return action_decision(*args, now=NOW, **kwargs)


def publication_classification(*args):
    return classify(*args, now=NOW)


class RoutinePublicationTests(unittest.TestCase):
    def test_complete_scoped_observation_continues_without_new_authorization(self):
        result = decide_action('publish_branch', TARGET, scope_authorized=True, publication=facts())
        self.assertEqual(result['decision'], 'CONTINUE')
        self.assertEqual(result['publication']['classification'], 'ROUTINE')
        self.assertFalse(result['prompt_user'])
        self.assertFalse(result['execution_authority'])
        self.assertFalse(result['publication']['execution_authority'])
        self.assertEqual(len(result['publication']['observation_sha256']), 64)

    def test_missing_raw_observation_and_applied_label_are_not_enough(self):
        for observation in (None, {'repository_rules': 'APPLIED'}, {'source': 'github_api'}):
            value = facts()
            value['rules_observation'] = observation
            result = decide_action('publish_branch', TARGET, scope_authorized=True,
                                   publication=value, authorization=AUTH)
            self.assertEqual(result['decision'], 'NEEDS_AUTHORIZATION')
        self.assertEqual(publication_classification(TARGET)['classification'], 'EXPLICIT')

    def test_foreign_refs_identities_stale_or_synthetic_rules_never_become_routine(self):
        def stale(value): value['rules_observation']['observed_at'] = '2026-09-15T11:00:00Z'
        def future(value): value['rules_observation']['observed_at'] = '2026-09-15T12:00:01Z'
        def partial(value): value['rules_observation']['complete'] = False
        def synthetic(value): value['rules_observation']['source'] = 'synthetic_fixture'
        def changed_id(value): value['rules_observation']['repository_response']['id'] = 102
        def bypass(value): value['rules_observation']['rulesets'][0]['bypass_actors'] = [{'actor_id': 1}]
        changes = [stale, future, partial, synthetic, changed_id, bypass,
                   lambda x: x.update(repository='other/project'),
                   lambda x: x.update(repository_id=102),
                   lambda x: x.update(repository_id=True),
                   lambda x: x.update(ref='refs/heads/main'),
                   lambda x: x.update(ref='refs/tags/v1'),
                   lambda x: x.update(ref='refs/heads/codex/TEST-8-routine-flow'),
                   lambda x: x.update(ref='refs/heads/feature/TEST-7-routine-flow'),
                   lambda x: x.update(ref='refs/heads/../main'),
                   lambda x: x.update(force=True), lambda x: x.update(delete=True),
                   lambda x: x.update(force=0),
                   lambda x: x['github'].update(host='https://other.example'),
                   lambda x: x['github'].update(base_branch='trunk'),
                   lambda x: x['github'].update(branch_pattern='{ticket.__class__}-{slug}'),
                   lambda x: x['github'].update(branch_pattern='codex/{ticket!r}-{slug}'),
                   lambda x: x['github'].update(branch_pattern='codex/{ticket}-{ticket}'),
                   lambda x: x.update(slug='../../../main')]
        for mutate in changes:
            value = facts()
            mutate(value)
            with self.subTest(change=repr(mutate), facts=value):
                result = publication_classification(TARGET, value)
                self.assertEqual(result['classification'], 'EXPLICIT', result)
                self.assertFalse(result['execution_authority'])

    def test_matching_other_destination_cannot_use_original_targets_routine_classification(self):
        value = facts()
        result = publication_classification('another/repository:' + REF, value)
        self.assertEqual(result['classification'], 'EXPLICIT')

    def test_empty_effective_rules_are_missing_and_need_existing_authority(self):
        value = facts()
        value['rules_observation'].update(branch_pages=[{'page': 1, 'rules': []}], rulesets=[])
        self.assertEqual(publication_classification(TARGET, value)['classification'], 'EXPLICIT')
        result = decide_action('publish_branch', TARGET, scope_authorized=True,
                               already_authorized=True, publication=value)
        self.assertEqual(result['decision'], 'CONTINUE')
        self.assertFalse(result['prompt_user'])

    def test_routine_classification_never_overrides_scope_platform_or_mode_prerequisites(self):
        for authority, expected in (({'scope_authorized': False, 'authorization': AUTH}, 'NEEDS_AUTHORIZATION'),
                                    ({'scope_authorized': True, 'platform_permitted': False}, 'PLATFORM_BLOCKED'),
                                    ({'scope_authorized': True, 'prerequisites_met': False}, 'RECONCILE')):
            result = decide_action('publish_branch', TARGET, publication=facts(), **authority)
            self.assertEqual(result['decision'], expected)
            self.assertFalse(result['execution_authority'])

    def test_jira_merge_and_governance_do_not_inherit_publication_classification(self):
        for kind in ('update_jira', 'merge', 'change_governance'):
            with self.assertRaises(ValidationError):
                decide_action(kind, TARGET, scope_authorized=True, publication=facts())
            result = decide_action(kind, TARGET, scope_authorized=True, authorization=AUTH)
            self.assertEqual(result['decision'], 'NEEDS_AUTHORIZATION')

    def test_custom_accepted_pattern_matches_exact_literal_ticket_and_slug(self):
        value = facts()
        value['github']['branch_pattern'] = 'work/{ticket}/{slug}'
        value['ref'] = 'refs/heads/work/TEST-7/routine-flow'
        self.assertEqual(publication_classification(REPO + ':' + value['ref'], value)['classification'], 'ROUTINE')

    def cli(self, data, now=NOW):
        spec = importlib.util.spec_from_file_location('awf_publication_status', ROOT / '.agentic/scripts/project_status.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out, err = io.StringIO(), io.StringIO()
        # Integrity has separate end-to-end coverage; exercise this CLI's input boundary.
        with patch.object(module, 'verify_installed'), patch.object(module, 'load', return_value=data), \
                patch.object(module, 'now_text', return_value=now), redirect_stdout(out), redirect_stderr(err):
            code = module.main(['--format', 'json', 'action', 'synthetic-action.json'])
        return code, json.loads(out.getvalue() or err.getvalue())

    def test_shipped_action_cli_accepts_publication_and_uses_current_clock(self):
        data = {'kind': 'publish_branch', 'target': TARGET, 'scope_authorized': True,
                'publication': facts(), 'authorization': AUTH}
        code, result = self.cli(data)
        self.assertEqual(code, 0)
        self.assertEqual(result['decision'], 'CONTINUE')
        code, result = self.cli(data, now='2026-09-15T13:00:00Z')
        self.assertEqual(code, 0)
        self.assertEqual(result['decision'], 'NEEDS_AUTHORIZATION')
        self.assertEqual(result['publication']['classification'], 'EXPLICIT')

    def test_saved_packet_cannot_override_action_clock(self):
        data = {'kind': 'publish_branch', 'target': TARGET, 'scope_authorized': True,
                'publication': facts(), 'authorization': AUTH, 'now': NOW}
        code, result = self.cli(data, now='2026-09-15T13:00:00Z')
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'REJECTED')
        data.pop('now')
        data['publication']['now'] = NOW
        code, result = self.cli(data, now='2026-09-15T13:00:00Z')
        self.assertEqual(result['decision'], 'NEEDS_AUTHORIZATION')

    def test_cycle_retains_explicit_publication_draft_and_current_clock(self):
        snapshot = load(ROOT / '.agentic/examples/project-cycle.json')
        snapshot['observed_at'] = '2026-09-15T13:00:00Z'
        item = snapshot['authorizations'][0]
        item['expires_at'] = '2026-09-15T14:00:00Z'
        item['decision'] = {'kind': 'publish_branch', 'target': TARGET, 'scope_authorized': True,
                            'publication': facts(), 'authorization': AUTH}
        result = project_cycle(snapshot, Contracts(ROOT / '.agentic/schemas'), snapshot['observed_at'])
        self.assertEqual(result['input_drafts'][0]['text'], AUTH['phrase'])
        decision = result['pending_authorizations'][0]['report']
        self.assertEqual(decision['publication']['classification'], 'EXPLICIT')
        self.assertEqual(decision['decision'], 'NEEDS_AUTHORIZATION')
        self.assertFalse(result['finish_allowed'])
        self.assertTrue(result['continue_actions'])


if __name__ == '__main__':
    unittest.main()
