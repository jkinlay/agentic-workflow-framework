"""Real-filesystem publication regressions, run only from the full source release."""
from pathlib import Path
import importlib.util
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import publish_catalog as publisher
from release_review import review_source
from release_self_test import reconcile_installed_acceptance

spec = importlib.util.spec_from_file_location('publication_discovery_fixture', ROOT / 'global/awf/tests/test_awf.py')
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='awf-publication-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = fixture_module.Fixture(self.root, version=publisher.VERSION)
        self.catalog = self.root / 'published.json'
        self.report_path = self.root / 'validation.json'
        test_result = {'status': 'PASS', 'elapsed_seconds': 1.25,
                       'tests': {'successful': True, 'run': 1, 'failures': 0, 'errors': 0}}
        self.report = {'status': 'PASS', 'validation_complete': True,
                       'template_version': publisher.VERSION, 'elapsed_seconds': 2.875,
                       'archive_sha256': publisher.sha256(self.fixture.archive.read_bytes()),
                       'manifest_sha256': publisher.sha256(self.fixture.content['MANIFEST.json']),
                       'checks': [{'check': 'synthetic passing validation', 'status': 'PASS'}],
                       'extracted_tests': test_result, 'installed_tests': dict(test_result)}
        self.reviews = self.root / 'synthetic-review.json'
        record = {'status':'PASS', 'scope_kind':'current_release', 'release':publisher.VERSION,
                  'review_id':'synthetic-fixture-not-real-review',
                  'reviewed_file_sha256':{n:publisher.sha256(b) for n,b in self.fixture.content.items() if n not in ('MANIFEST.json','MANIFEST.md')}}
        self.reviews.write_text(json.dumps(record),encoding='utf-8')
        self.review_pin = publisher.sha256(self.reviews.read_bytes())
        checked = review_source(self.fixture.source,self.report['manifest_sha256'],self.reviews,self.review_pin)
        self.report['current_review'] = checked
        self.report['extracted_tests'].update(current_review=checked,release_qualified=True)
        self.reconcile_report()

    def publish(self):
        self.report_path.write_text(json.dumps(self.report), encoding='utf-8')
        return publisher.publish(self.fixture.source, self.fixture.archive, self.report_path, self.catalog,
                                 self.reviews,self.review_pin)

    def test_missing_or_changed_review_cannot_publish(self):
        for block in ('current_review','source_review','checks_only'):
            with self.subTest(block=block):
                saved=json.loads(json.dumps(self.report))
                if block=='current_review':self.report.pop('current_review')
                elif block=='source_review':self.report['extracted_tests']['current_review']={'status':'PASS'}
                else:self.report['extracted_tests']['current_review']={
                    **self.report['current_review'], 'release_qualified': False}
                with self.assertRaisesRegex(publisher.ValidationError,'current release review'):
                    self.publish()
                self.assertFalse(self.catalog.exists())
                self.report=saved
        self.reviews.write_bytes(self.reviews.read_bytes()+b'\n')
        with self.assertRaisesRegex(publisher.ValidationError,'digest mismatch'):
            self.publish()
        self.assertFalse(self.catalog.exists())

    def test_fractional_metadata_publishes_actual_verified_files(self):
        result = self.publish()
        self.assertEqual('PUBLISHED', result['status'])
        self.assertFalse(result['target_projects_changed'])
        proof = fixture_module.awf.locate(self.catalog, publisher.VERSION)
        self.assertEqual('VERIFIED_LOCAL_RELEASE', proof['status'])
        self.assertEqual(self.report['manifest_sha256'], proof['manifest_sha256'])

    def reconcile_report(self):
        source = self.report['extracted_tests']
        source.update(scope='source-suite', suite_complete=False, release_qualified=False,
                      source_manifest_sha256=self.report['manifest_sha256'], plan_sha256='a' * 64,
                      shard_count=9, coverage_reconciliation={
                          'status': 'PENDING_INSTALLED_ACCEPTANCE', 'deferred_coverage': {
                              'scripts/tests/test_installed_self_test.py': 'installed-acceptance'}})
        installed = self.report['installed_tests']
        installed.update(scope='installed-suite', suite_complete=True,
                         source_manifest_sha256=self.report['manifest_sha256'], plan_sha256='b' * 64,
                         shard_count=9, shard_reports=[{'shard': n} for n in range(9)],
                         coverage_reconciliation={'status': 'PASS', 'deferred_coverage': {}})
        self.report['coverage_reconciliation'] = reconcile_installed_acceptance(source, installed)

    def test_reconciled_deferred_source_publishes(self):
        self.reconcile_report()
        self.assertIs(self.report['extracted_tests']['release_qualified'], False)
        self.assertEqual('PUBLISHED', self.publish()['status'])

    def test_reconciled_report_cannot_hide_wrong_review_or_incomplete_installed_suite(self):
        for field, value in [('suite_complete', False), ('source_manifest_sha256', 'c' * 64)]:
            with self.subTest(field=field):
                self.reconcile_report()
                self.report['installed_tests'][field] = value
                with self.assertRaises(publisher.ValidationError):
                    self.publish()
                self.assertFalse(self.catalog.exists())

    def test_missing_tampered_or_unqualified_reconciliation_cannot_publish(self):
        saved = json.loads(json.dumps(self.report))
        for mutation in ('missing', 'tampered', 'unqualified', 'raw_qualified'):
            with self.subTest(mutation=mutation):
                self.report = json.loads(json.dumps(saved))
                if mutation == 'missing':
                    self.report.pop('coverage_reconciliation')
                elif mutation == 'tampered':
                    self.report['coverage_reconciliation']['installed_plan_sha256'] = 'c' * 64
                elif mutation == 'unqualified':
                    self.report['coverage_reconciliation']['release_qualified'] = False
                else:
                    self.report['extracted_tests'].update(release_qualified=True, suite_complete=True)
                with self.assertRaises(publisher.ValidationError):
                    self.publish()
                self.assertFalse(self.catalog.exists())

    def test_previous_catalog_preserved_and_repeat_publication_idempotent(self):
        previous = json.loads(self.fixture.catalog.read_bytes())
        previous['latest']['version'] = '1.4.1'
        old = fixture_module.encoded(previous)
        self.catalog.write_bytes(old)
        self.publish()
        history = self.root / '.awf-catalog-history' / (publisher.sha256(old) + '.json')
        self.assertEqual(old, history.read_bytes())
        accepted = self.catalog.read_bytes()
        self.publish()
        self.assertEqual(accepted, self.catalog.read_bytes())
        self.assertEqual([history], list(history.parent.iterdir()))

    def test_provisional_pass_preserves_existing_catalog(self):
        old = self.fixture.catalog.read_bytes()
        self.catalog.write_bytes(old)
        del self.report['validation_complete']
        with self.assertRaises(publisher.ValidationError):
            self.publish()
        self.assertEqual(old, self.catalog.read_bytes())
        self.assertFalse((self.root / '.awf-catalog-history').exists())

    def test_cannot_publish_over_newer_catalog(self):
        previous = json.loads(self.fixture.catalog.read_bytes())
        previous['latest']['version'] = '99.0.0'
        old = fixture_module.encoded(previous)
        self.catalog.write_bytes(old)
        with self.assertRaises(publisher.ValidationError):
            self.publish()
        self.assertEqual(old, self.catalog.read_bytes())

    def test_missing_or_failed_test_counts_cannot_publish(self):
        for field, value in [('run', 0), ('run', True), ('run', 1.0),
                             ('failures', 1), ('failures', False), ('errors', None)]:
            with self.subTest(field=field, value=value):
                counts = self.report['extracted_tests']['tests']
                saved = counts[field]
                counts[field] = value
                with self.assertRaises(publisher.ValidationError):
                    self.publish()
                self.assertFalse(self.catalog.exists())
                counts[field] = saved

    def test_duplicate_keys_and_nonfinite_metadata_rejected(self):
        for raw in ['{"status":"FAILED","status":"PASS"}',
                    '{"elapsed_seconds":NaN}', '{"elapsed_seconds":Infinity}',
                    '{"elapsed_seconds":-Infinity}', '{"elapsed_seconds":1e999}']:
            with self.subTest(raw=raw):
                self.report_path.write_text(raw, encoding='utf-8')
                with self.assertRaises(publisher.ValidationError):
                    publisher.read_validation_report(self.report_path)


if __name__ == '__main__':
    unittest.main()
