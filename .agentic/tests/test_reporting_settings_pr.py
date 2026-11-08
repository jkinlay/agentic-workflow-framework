"""Reporting PR policy regressions in real disposable repositories; no services."""
from copy import deepcopy
from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import source_only
from agentic import ValidationError
from agentic import operating as op
from agentic.canonical import fingerprint
from agentic.protected_settings import effective_snapshot, require_budget_policy, audit_matches
from agentic.reporting.settings import effective_reporting
import test_awf242_settings_pr as fixture

class ReportingSettingsPRTests(unittest.TestCase):
    def setUp(self):
        fixture.SettingsPRTests.setUp(self)
        self.value['reporting'] = effective_reporting({})
        self.gov['github']['repository_id']=101
        (self.root/op.GOVERNANCE).write_bytes(op._json(self.gov))
        self.publish(self.value)
    git = fixture.SettingsPRTests.git
    write = fixture.SettingsPRTests.write
    publish = fixture.SettingsPRTests.publish

    def bindings(self):
        return [dict(project=self.gov['project']['id'], repository=str(self.gov['github']['repository_id']),
            scope_revision='accepted-scope', platform=p, destination='approved-'+p, revision='1',
            secret_ref='env:REPORT_TEST_SECRET') for p in ('teams','slack')]

    def enabled(self):
        value=deepcopy(self.value)
        value['reporting']=dict(mode='both', bindings=self.bindings())
        return value

    def test_working_and_pr_edits_in_both_directions_and_every_binding_field(self):
        enabled=self.enabled()
        edits=[self.value, {k:v for k,v in self.value.items() if k!='reporting'}]
        for field in self.bindings()[0]:
            edit=deepcopy(enabled)
            replacements=dict(project='foreign',repository='102',scope_revision='other',platform='slack',
                destination='replacement',revision='2',secret_ref='env:OTHER')
            edit['reporting']['bindings'][0][field]=replacements[field]
            edits.append(edit)
        edits.extend([{**enabled,'reporting':dict(mode='off',bindings=[])},
            {**enabled,'reporting':dict(mode='both',bindings=list(reversed(self.bindings())))}])
        for accepted, changes in ((self.value,[enabled]),(enabled,edits)):
            self.publish(accepted)
            for edit in changes:
                self.write(edit)
                snapshot=op.read_operating(self.root,self.gov)
                self.assertEqual(effective_reporting(accepted),effective_reporting(snapshot.config))
            self.git('checkout','-B','codex/reporting')
            self.write(changes[0]);self.git('add',op.CONFIG);self.git('commit','-qm','Proposed reporting','--allow-empty')
            self.assertEqual(effective_reporting(accepted),effective_reporting(op.read_operating(self.root,self.gov).config))

    def test_merged_settings_take_effect_next_load_with_commit(self):
        commit=self.publish(self.enabled())
        self.write(self.value)
        snapshot=op.read_operating(self.root,self.gov)
        self.assertEqual(self.enabled()['reporting'],snapshot.config['reporting'])
        self.assertEqual(commit,snapshot.provenance['protected_settings']['commit'])
        commit=self.publish(self.value)
        self.write(self.enabled())
        snapshot=op.read_operating(self.root,self.gov)
        self.assertEqual('off',effective_reporting(snapshot.config)['mode'])
        self.assertEqual(commit,snapshot.provenance['protected_settings']['commit'])

    def test_reporting_source_labels_follow_accepted_domain_presence(self):
        for present in (False, True):
            with self.subTest(accepted_reporting_present=present):
                accepted = deepcopy(self.value)
                if not present:
                    accepted.pop('reporting')
                self.publish(accepted)
                self.write(self.enabled())
                snapshot = op.read_operating(self.root, self.gov)
                self.assertEqual('default branch' if present else 'legacy retained; default branch',
                    snapshot.provenance['protected_settings']['sources']['reporting'])
                self.assertEqual(dict(mode='off', bindings=[]), effective_reporting(snapshot.config))
        self.publish(self.value)
        self.git('rm', '-q', op.CONFIG)
        self.git('commit', '-qm', 'Accepted absent reporting file')
        self.git('update-ref', 'refs/remotes/origin/main', 'HEAD')
        self.write(self.enabled())
        snapshot = op.read_operating(self.root, self.gov)
        self.assertEqual('fresh defaults', snapshot.provenance['protected_settings']['sources']['reporting'])
        self.assertEqual(dict(mode='off', bindings=[]), effective_reporting(snapshot.config))

    def test_absent_empty_and_explicit_off_normalize_without_git(self):
        from agentic.protected_settings import resolve_protected
        with patch('agentic.protected_settings._git',side_effect=AssertionError('Git forbidden')):
            for block in (None,{},dict(mode='off'),dict(mode='off',bindings=[])):
                value=deepcopy(self.value)
                if block is None:value.pop('reporting',None)
                else:value['reporting']=block
                accepted,_=resolve_protected(self.root/'not-a-repo',self.gov,value)
                self.assertEqual(dict(mode='off',bindings=[]),effective_reporting(accepted))

    def test_unverified_planted_binding_refuses_before_provider(self):
        self.git('remote','add','origin','https://example.invalid/project.git')
        self.git('symbolic-ref','--delete','refs/remotes/origin/HEAD')
        self.write(self.enabled())
        snapshot=op.read_operating(self.root,self.gov)
        with self.assertRaisesRegex(ValidationError,'unverified'):
            require_budget_policy(snapshot)

    def test_legacy_and_pre_reporting_projection_audit_bridge(self):
        from agentic.protected_settings import unprotected_hash
        value=self.enabled()
        for excluded in ((),('budgeting',),('budgeting','operating_calendar')):
            event=dict(after_hash=fingerprint('operating',value))
            if excluded:
                event['unprotected_after_hash']=fingerprint('operating-unprotected',
                    {k:v for k,v in value.items() if k not in excluded})
            self.assertTrue(audit_matches(value,event),excluded)
            changed=deepcopy(value);changed['streams']['count']=2
            self.assertFalse(audit_matches(changed,event),excluded)

    def test_historical_records_survive_accepted_reporting_change_and_next_local_audit(self):
        import tempfile
        from settings_pr_fixtures import publish_fixture_settings
        for projection in (False, True):
            with self.subTest(pre_reporting_projection=projection), tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                publish_fixture_settings(self,root,self.gov,self.value)
                snapshot=op.set_operating(root,self.gov,'Two streams',['streams.count=2'])
                path=next((root/op.STATE/'changes').glob('*.json'))
                event=json.loads(path.read_bytes())
                event.pop('unprotected_before_hash',None)
                event.pop('unprotected_after_hash',None)
                if projection:
                    event['unprotected_after_hash']=fingerprint('operating-unprotected',
                        {k:v for k,v in snapshot.config.items() if k not in ('budgeting','operating_calendar')})
                path.write_bytes(op._json(event));retained=path.read_bytes()
                accepted=snapshot.config
                accepted['reporting']=self.enabled()['reporting']
                publish_fixture_settings(self,root,self.gov,accepted)
                self.assertEqual('both',op.read_operating(root,self.gov).config['reporting']['mode'])
                op.set_operating(root,self.gov,'One stream',['streams.count=1'])
                self.assertEqual('both',op.read_operating(root,self.gov).config['reporting']['mode'])
                self.assertEqual(retained,path.read_bytes())

    def test_pre_reporting_projection_replays_retained_bindings_after_checkout_update(self):
        self.publish(self.enabled())
        first=op.set_operating(self.root,self.gov,'Two streams',['streams.count=2'])
        paths=sorted((self.root/op.STATE/'changes').glob('*.json'))
        event=json.loads(paths[0].read_bytes())
        # An old owner-confirmed reporting change retained these exact values.
        for field in ('mode','bindings'):
            event['changes'].append(dict(path='reporting.'+field,before=self.value['reporting'][field],after=first.config['reporting'][field]))
        event['before_hash']=fingerprint('operating',self.value)
        event['unprotected_before_hash']=fingerprint('operating-unprotected',
            {k:v for k,v in self.value.items() if k not in ('budgeting','operating_calendar')})
        event['unprotected_after_hash']=fingerprint('operating-unprotected',
            {k:v for k,v in first.config.items() if k not in ('budgeting','operating_calendar')})
        paths[0].write_bytes(op._json(event))
        second=op.set_operating(self.root,self.gov,'One stream',['streams.count=1'])
        last=next(p for p in (self.root/op.STATE/'changes').glob('*.json') if p not in paths)
        event=json.loads(last.read_bytes())
        event['unprotected_after_hash']=fingerprint('operating-unprotected',
            {k:v for k,v in second.config.items() if k not in ('budgeting','operating_calendar')})
        last.write_bytes(op._json(event))
        retained={p:p.read_bytes() for p in (self.root/op.STATE/'changes').glob('*.json')}
        for mode in ('off','both'):
            value=second.config
            value['reporting']['mode']=mode
            value['reporting']['bindings'][0].update(destination='replacement',revision='2',secret_ref='env:OTHER')
            commit=self.publish(value)
            snapshot=op.read_operating(self.root,self.gov)
            self.assertEqual(value['reporting'],snapshot.config['reporting'])
            self.assertEqual(commit,snapshot.provenance['protected_settings']['commit'])
            self.assertEqual(retained,{p:p.read_bytes() for p in retained})
            self.assertEqual('ACCEPTED',op.plan_initialize_operating(self.root,self.gov)['inspection']['status'])
        value['streams']['count']=2
        self.write(value)
        with self.assertRaisesRegex(ValidationError,'last audited'):
            op.read_operating(self.root,self.gov)

    def test_proposed_file_offline_validation_and_no_echo_every_binding_field(self):
        proposed=self.root/'proposed.yaml'
        invalid=[dict(mode='bad',bindings=[]),dict(mode='both',bindings=[]),[],None,
            dict(mode='off',bindings='bad'),dict(mode='off',bindings=[self.bindings()[0]]*2),
            dict(mode='off',bindings=self.bindings()*2),dict(mode='off',unexpected=True)]
        sentinel='ghp_'+'S'*36
        for field in self.bindings()[0]:
            bindings=self.bindings();bindings[0][field]=sentinel
            invalid.append(dict(mode='both',bindings=bindings))
        for field in self.bindings()[0]:
            for bad in ('', ' leading', 'trailing ', 'a'*257, 'line\nbreak', 7, None):
                bindings=self.bindings();bindings[0][field]=bad
                invalid.append(dict(mode='off',bindings=bindings))
        for item in ({},dict(self.bindings()[0],extra='bad'), 'bad'):
            invalid.append(dict(mode='off',bindings=[item]))
        invalid.extend([dict(mode='both',bindings=[dict(self.bindings()[0],project='foreign'),self.bindings()[1]]),
            dict(mode='both',bindings=[dict(self.bindings()[0],repository='102'),self.bindings()[1]])])
        with ExitStack() as stack:
            for target in ('socket.socket','socket.create_connection','urllib.request.urlopen',
                'agentic.reporting.platform.environment_secret','agentic.reporting.platform.build_reporting_providers',
                'agentic.reporting.platform.SlackReportingAdapter','agentic.reporting.platform.TeamsReportingAdapter',
                'agentic.reporting.http_transport.SlackTransport','agentic.reporting.http_transport.TeamsTransport',
                'agentic.protected_settings._git'):
                stack.enter_context(patch(target,side_effect=AssertionError(target+' forbidden')))
            for block in [self.enabled()['reporting'],*invalid]:
                proposed.write_bytes(op._json({**self.value,'reporting':block}))
                out=io.StringIO()
                with redirect_stdout(out):code=op.main(['--root',str(self.root),'validate','--file',str(proposed),'--json'])
                self.assertEqual(0 if block==self.enabled()['reporting'] else 2,code,out.getvalue())
                self.assertNotIn(sentinel,out.getvalue())
                if code==0:self.assertIn('unobserved',out.getvalue().lower())
            for raw in ('reporting: {"'+sentinel+'": 1,"'+sentinel+'": 2}', 'reporting: ["'+sentinel+'"'):
                proposed.write_text(raw)
                out=io.StringIO()
                with redirect_stdout(out):code=op.main(['--root',str(self.root),'validate','--file',str(proposed),'--json'])
                self.assertEqual(2,code);self.assertNotIn(sentinel,out.getvalue())

    def test_unsafe_working_and_default_diagnostics_do_not_echo(self):
        sentinel='ghp_'+'X'*36
        for field in self.bindings()[0]:
            unsafe=self.enabled();unsafe['reporting']['bindings'][0][field]=sentinel
            self.write(unsafe)
            out=io.StringIO()
            with redirect_stdout(out):op.main(['--root',str(self.root),'show','--json'])
            self.assertNotIn(sentinel,out.getvalue())
            self.publish(unsafe);self.write(self.value)
            out=io.StringIO()
            with redirect_stdout(out):code=op.main(['--root',str(self.root),'show','--json'])
            self.assertEqual(2,code);self.assertNotIn(sentinel,out.getvalue())
            self.publish(self.value)

    def test_protected_set_names_pr_and_writes_nothing(self):
        before=(self.root/op.CONFIG).read_bytes()
        with self.assertRaisesRegex(ValidationError,'PR'):
            op.set_operating(self.root,self.gov,'Turn reporting off',['reporting.mode=off'])
        self.assertEqual(before,(self.root/op.CONFIG).read_bytes())
        self.assertFalse((self.root/'.agentic-state').exists())
