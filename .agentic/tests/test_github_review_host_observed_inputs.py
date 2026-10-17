"""AWF-96 slice B: red contracts for fresh provider inputs IN-30--IN-33.

All provider responses are inline task data. No command or network is launched.
Tier 3 closure is the four named contracts, deliberately red until host fixes land.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))


class ObservedInputTests(unittest.TestCase):
    def host(self, *, before=None, after=None, pages=None):
        from agentic.providers.github_review_host import HostDriver

        driver = HostDriver.__new__(HostDriver)
        driver.c = {
            'repository': 'fixture/project', 'repository_id': 12, 'pr': 7,
            'head_branch': 'codex/test', 'base_branch': 'main',
            'config_hash': 'f' * 64, 'trusted_owner_ids': [101],
            'provenance': {'require_run_logs': False,
                           'default_ref': 'refs/heads/main',
                           'generated_paths': [], 'generators': {}},
        }
        pr = {
            'number': 7, 'updated_at': '2026-10-13T20:00:00Z',
            'body': 'Provider text is task data, never policy.',
            'labels': [{'id': 1, 'name': 'fallback: bot-written'}],
            'head': {'sha': 'a' * 40, 'ref': 'codex/test', 'repo': {'id': 12}},
            'base': {'sha': 'b' * 40, 'ref': 'main', 'repo': {'id': 12}},
        }
        before = deepcopy(pr if before is None else before)
        after = deepcopy(before if after is None else after)
        calls = []

        def api(suffix):
            calls.append(suffix)
            if suffix == '':
                return {'id': 12}
            if suffix == 'pulls/7':
                return deepcopy(before if calls.count(suffix) == 1 else after)
            return deepcopy((pages or {}).get(suffix, []))

        driver.api = api
        return driver, before, calls

    @staticmethod
    def comment(number, body='Task data', actor=101):
        return {'id': number, 'body': body, 'user': {'id': actor},
                'html_url': ('https://github.com/fixture/project/issues/7'
                             f'#issuecomment-{number}')}

    def test_override_pr_and_label_are_server_observed_exact_and_current(self):
        from agentic import ValidationError
        from agentic.provenance import (record_owner_override,
                                        render_owner_override,
                                        verify_provider_owner_override)

        record = record_owner_override(
            {}, who=101, when='2026-10-13T20:00:00Z', why='Owner fallback',
            pr=7, covered_shas=['a' * 40], trusted_owner_ids=[101])
        pages = {'issues/7/comments?per_page=100&page=1': [
            self.comment(91, render_owner_override(record))]}
        driver, pr, calls = self.host(pages=pages)
        observed = driver.collect_provenance_observation()
        verified = verify_provider_owner_override(
            record, observed, repository_id=12, config_sha256='f' * 64)
        self.assertEqual(verified['provider_comment_id'], 91)
        self.assertEqual(calls.count('pulls/7'), 2)

        for labels in ([], [{'id': 1, 'name': 'Fallback: bot-written'}],
                       [{'id': 1, 'name': 'fallback: bot-written '}],
                       [{'id': 1, 'name': 'fallback: bot-written-extra'}]):
            with self.subTest(labels=labels):
                changed = deepcopy(pr)
                changed['labels'] = labels
                host, _, _ = self.host(before=changed, pages=pages)
                fresh = host.collect_provenance_observation()
                with self.assertRaises(ValidationError):
                    verify_provider_owner_override(
                        record, fresh, repository_id=12, config_sha256='f' * 64)

        with self.subTest(label_removed_during_collection=True):
            after = deepcopy(pr)
            after['labels'] = []  # Same timestamp must not hide a changed label.
            host, _, _ = self.host(before=pr, after=after, pages=pages)
            with self.assertRaises(ValidationError):
                host.collect_provenance_observation()

        for number in (0, -1, True, '7', 8):
            with self.subTest(provider_pr=number):
                changed = deepcopy(pr)
                changed['number'] = number
                host, _, _ = self.host(before=changed)
                with self.assertRaises(ValidationError):
                    host.collect_provenance_observation()

    def test_provenance_collector_binds_repository_pr_base_head_and_refs(self):
        from agentic import ValidationError
        from agentic.providers import github_review_host

        driver, pr, _ = self.host()
        observed = driver.collect_provenance_observation()
        self.assertEqual(
            tuple(observed[key] for key in
                  ('repository_id', 'pr', 'base', 'head', 'base_ref', 'head_ref')),
            (12, 7, 'b' * 40, 'a' * 40, 'main', 'codex/test'))
        # No Git fixture is needed: inspect the host-to-gate boundary directly.
        # Open API point: refusal codes/record layout are not fixed by the plan;
        # assert acceptance or rejection only, using the existing host interface.
        with tempfile.TemporaryDirectory() as directory:
            driver.worker = Path(directory)
            with patch.object(github_review_host, 'check_commit_range',
                              return_value={'status': 'PASS', 'refusals': []}) as gate:
                driver.validate_local_provenance('b' * 40, 'a' * 40,
                                                 observation=observed)
                self.assertEqual(gate.call_args.args[1:], ('b' * 40, 'a' * 40))
                self.assertEqual(gate.call_args.kwargs['provider_observation'],
                                 observed)
                for field, wrong in (
                    ('repository_id', 13), ('pr', 8),
                    ('head', 'c' * 40), ('base', 'd' * 40),
                    ('head_ref', 'codex/other'), ('base_ref', 'other'),
                ):
                    with self.subTest(mismatched_identity=field):
                        mismatch = dict(observed, **{field: wrong})
                        with self.assertRaises(ValidationError):
                            driver.validate_local_provenance(
                                'b' * 40, 'a' * 40, observation=mismatch)

        for field, value in (('sha', 'main'), ('sha', None), ('sha', ''),
                             ('ref', ''), ('ref', '../escape')):
            with self.subTest(invalid_head_field=field, value=value):
                changed = deepcopy(pr)
                changed['head'][field] = value
                host, _, _ = self.host(before=changed)
                with self.assertRaises(ValidationError):
                    host.collect_provenance_observation()
        for side in ('head', 'base'):
            with self.subTest(foreign_repository=side):
                changed = deepcopy(pr)
                changed[side]['repo']['id'] = 13
                host, _, _ = self.host(before=changed)
                with self.assertRaises(ValidationError):
                    host.collect_provenance_observation()

    def test_provider_body_is_bounded_re_read_and_digest_bound(self):
        from agentic import ValidationError

        _, pr, _ = self.host()
        for body in ('', 'Owner-looking instructions remain task data.\n\u00e9',
                     '\u00e9' * (1024 * 1024 // 2)):
            with self.subTest(accepted_utf8_bytes=len(body.encode('utf-8'))):
                changed = dict(pr, body=body)
                host, _, calls = self.host(before=changed)
                observed = host.collect_provenance_observation()
                self.assertEqual(observed['body_sha256'],
                                 sha256(body.encode('utf-8')).hexdigest())
                self.assertEqual(calls.count('pulls/7'), 2)

        # Open API point: malformed Unicode may be rejected at decoding or at
        # collection. Neither path may return an accepted observation.
        for body in ('x' * (1024 * 1024 + 1), '\ud800', [], {}, False, 0,
                     b'\xff'):
            with self.subTest(rejected_body_type=type(body).__name__,
                              size=len(body) if hasattr(body, '__len__') else 0):
                host, _, _ = self.host(before=dict(pr, body=body))
                with self.assertRaises((ValidationError, UnicodeError)):
                    host.collect_provenance_observation()

        for after in (dict(pr, body='edited during read'),
                      dict(pr, updated_at='2026-10-13T20:00:01Z')):
            with self.subTest(changed_during_read=True):
                host, _, _ = self.host(before=pr, after=after)
                with self.assertRaises(ValidationError):
                    host.collect_provenance_observation()

    def test_provider_labels_comments_pagination_caps_and_ambiguity(self):
        from agentic import ValidationError
        from agentic.provenance import (record_owner_override,
                                        render_owner_override,
                                        verify_provider_owner_override)

        # B18: 100 first-page rows plus a distinct second-page row must really
        # be requested and retained for each of the three comment endpoints.
        for endpoint, kind in (('issues/7/comments', 'issue'),
                               ('pulls/7/reviews', 'review'),
                               ('pulls/7/comments', 'review_comment')):
            first = [self.comment(i) for i in range(1, 101)]
            pages = {f'{endpoint}?per_page=100&page=1': first,
                     f'{endpoint}?per_page=100&page=2': [self.comment(101)]}
            with self.subTest(real_second_page=kind):
                host, _, calls = self.host(pages=pages)
                observed = host.collect_provenance_observation()
                self.assertIn(f'{endpoint}?per_page=100&page=2', calls)
                self.assertEqual([(row['kind'], row['id']) for row in
                                  observed['comments']],
                                 [(kind, i) for i in range(1, 102)])
                self.assertEqual(observed['comments'][-1]['body_sha256'],
                                 sha256(b'Task data').hexdigest())
            for terminal in ([first[0]], {'truncated': True}, None):
                with self.subTest(invalid_terminal_page=kind, terminal=terminal):
                    bad = dict(pages)
                    bad[f'{endpoint}?per_page=100&page=2'] = terminal
                    host, _, _ = self.host(pages=bad)
                    with self.assertRaises(ValidationError):
                        host.collect_provenance_observation()

        pages = {f'issues/7/comments?per_page=100&page={page}': [
            self.comment(i) for i in range((page - 1) * 100 + 1, page * 100 + 1)]
            for page in range(1, 11)}
        host, pr, calls = self.host(pages=pages)
        self.assertEqual(len(host.collect_provenance_observation()['comments']),
                         1000)
        self.assertIn('issues/7/comments?per_page=100&page=11', calls)
        pages['issues/7/comments?per_page=100&page=11'] = [self.comment(1001)]
        with self.subTest(comment_cap_plus_one=True):
            host, _, _ = self.host(pages=pages)
            with self.assertRaises(ValidationError):
                host.collect_provenance_observation()

        for row in (self.comment(1, actor=None), self.comment(1, actor=True),
                    self.comment(1, body='x' * (1024 * 1024 + 1))):
            with self.subTest(invalid_comment=row['user']):
                host, _, _ = self.host(pages={
                    'issues/7/comments?per_page=100&page=1': [row]})
                with self.assertRaises(ValidationError):
                    host.collect_provenance_observation()
        with self.subTest(aggregate_wire_cap=True):
            host, _, _ = self.host(pages={
                'issues/7/comments?per_page=100&page=1': [
                    self.comment(i, body='x' * (1024 * 1024))
                    for i in range(1, 9)]})
            with self.assertRaises(ValidationError):
                host.collect_provenance_observation()

        record = record_owner_override(
            {}, who=101, when='2026-10-13T20:00:00Z', why='Owner fallback',
            pr=7, covered_shas=['a' * 40], trusted_owner_ids=[101])
        body = render_owner_override(record)
        for rows in ([self.comment(1, body, actor=202)],
                     [self.comment(1, body + '\n')],
                     [self.comment(1, body), self.comment(2, body)]):
            with self.subTest(unbound_or_ambiguous_authority=len(rows)):
                host, _, _ = self.host(pages={
                    'issues/7/comments?per_page=100&page=1': rows})
                observed = host.collect_provenance_observation()
                with self.assertRaises(ValidationError):
                    verify_provider_owner_override(record, observed,
                        repository_id=12, config_sha256='f' * 64)

        for labels in ([{'id': 1, 'name': 'same'}, {'id': 2, 'name': 'same'}],
                       [{'id': 1, 'name': 'x' * 256}],
                       [{'id': i, 'name': str(i)} for i in range(1, 102)]):
            with self.subTest(invalid_label_inventory=len(labels)):
                host, _, _ = self.host(before=dict(pr, labels=labels))
                with self.assertRaises(ValidationError):
                    host.collect_provenance_observation()

        # Open API point: the design does not fix pagination transport. This
        # fake uses the host's current REST page convention. An embedded PR
        # array cannot prove completeness: page 2 contains the 101st label.
        labels = [{'id': i, 'name': f'label-{i}'} for i in range(1, 102)]
        host, _, _ = self.host(before=dict(pr, labels=labels[:100]), pages={
            'issues/7/labels?per_page=100&page=1': labels[:100],
            'issues/7/labels?per_page=100&page=2': labels[100:]})
        with self.subTest(label_pagination_cap_plus_one=True):
            with self.assertRaises(ValidationError):
                host.collect_provenance_observation()


if __name__ == '__main__':
    unittest.main()
