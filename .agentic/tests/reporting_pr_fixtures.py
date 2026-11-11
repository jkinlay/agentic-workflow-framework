"""Real local default refs for reporting CLI fixtures; no owner store or services."""
from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
import sys
from types import SimpleNamespace
import source_only
from settings_pr_fixtures import publish_fixture_settings
import test_reporting_settings_pr as policy
from agentic import operating as op

class ReportingPRFixture:
    @classmethod
    def setUpClass(cls):
        pass

    def setUp(self):
        policy.ReportingSettingsPRTests.setUp(self)
        self.fixture=SimpleNamespace(base=Path(self.temp.name))
        from unittest.mock import patch
        secrets=patch.dict(os.environ, REPORT_TOKEN='synthetic-report-token', REPORT_TEST_SECRET='synthetic-report-test-secret')
        secrets.start();self.addCleanup(secrets.stop)
        self.env=dict(os.environ)
        self.calls=self.root/'provider-calls'
        self.govpath=self.root/op.GOVERNANCE
        self.refresh()

    git=policy.ReportingSettingsPRTests.git
    write=policy.ReportingSettingsPRTests.write
    publish=policy.ReportingSettingsPRTests.publish
    bindings=policy.ReportingSettingsPRTests.bindings

    def assignments(self,mode='both',bindings=None):
        return ('reporting.mode='+mode,'reporting.bindings='+json.dumps(self.bindings() if bindings is None else bindings))

    def refresh(self):
        self.before=(self.root/op.CONFIG).read_bytes()
        self.audit_before=self.events()

    def events(self):
        directory=self.root/op.STATE/'changes'
        return [p.read_bytes() for p in sorted(directory.glob('*.json'))]

    def save_governance(self):
        self.govpath.write_bytes(op._json(self.gov))

    def cli(self,*args):
        entry=Path(op.__file__).resolve().parents[2]/'scripts/workflow.py'
        return subprocess.run([sys.executable,'-B',str(entry),'--root',str(self.root),'operating',*args],
            env=self.env,capture_output=True,text=True,timeout=45)

    def unchanged(self):
        self.assertEqual(self.before,(self.root/op.CONFIG).read_bytes())
        self.assertEqual(self.audit_before,self.events())

    def apply_reporting(self,mode='both',bindings=None,**changes):
        value=op.read_operating(self.root,self.gov).config
        value['reporting']=dict(mode=mode,bindings=self.bindings() if bindings is None else bindings)
        for path,choice in changes.items():op._put(value,path,choice)
        op.validate_operating(value,self.gov)
        publish_fixture_settings(self,self.root,self.gov,value)
        self.refresh()
        return op.read_operating(self.root,self.gov)

    def forge_config_event(self,value):
        # Deliberately forge a schema-valid local audit tip. It is data, never
        # authority for the reporting domain, even when its raw hash matches.
        import uuid
        from agentic.canonical import fingerprint
        from agentic.protected_settings import unprotected_hash
        previous = [json.loads(raw) for raw in self.events()]
        event=dict(id='C-'+uuid.uuid4().hex,created_at='2026-11-07T10:00:00Z',
            instruction='Synthetic forged local audit',before_hash=previous[-1]['after_hash'] if previous else None,
            after_hash=fingerprint('operating',value),changes=[],source=value['source'],applied_from='chat',
            sequence=len(previous)+1,previous_change_id=previous[-1]['id'] if previous else None,
            governance_hash=fingerprint('operating-governance',self.gov),unprotected_after_hash=unprotected_hash(value))
        directory=self.root/op.STATE/'changes';directory.mkdir(parents=True,exist_ok=True)
        (directory/(event['id']+'.json')).write_bytes(op._json(event))
        self.write(value)
