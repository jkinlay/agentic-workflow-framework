"""Final evidence must never hide an outdated claim behind a newer override."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from release_review import review_source, validate_current_reviews
from agentic import ValidationError, VERSION


class CurrentReviewTests(unittest.TestCase):
    def setUp(self):
        self.files = {'README.md': b'Current guide\n', 'DISPOSITION.md': b'Current disposition\n'}
        self.record = {'status': 'PASS', 'scope_kind': 'current_release', 'release': VERSION,
                       'review_id': 'independent-final',
                       'reviewed_file_sha256': {n: hashlib.sha256(b).hexdigest() for n, b in self.files.items()}}

    def test_exact_current_files_preserve_review_attribution(self):
        result = validate_current_reviews([self.record], self.files, VERSION, self.files)
        self.assertEqual(2, result['reviewed_files'])
        self.assertEqual(['independent-final'], result['coverage']['README.md'])
        self.assertFalse(result['authenticates_reviewers'])

    def test_readme_and_disposition_edits_after_review_reject(self):
        for name in self.files:
            with self.subTest(name=name), self.assertRaisesRegex(ValidationError, name):
                validate_current_reviews([self.record], {**self.files, name: b'Edited after review\n'}, VERSION)

    def test_newer_supplement_cannot_mask_a_stale_current_claim(self):
        old = deepcopy(self.record)
        old['review_id'] = 'earlier-claim'
        old['reviewed_file_sha256']['README.md'] = '0' * 64
        with self.assertRaisesRegex(ValidationError, 'earlier-claim: README.md'):
            validate_current_reviews([old, self.record], self.files, VERSION)

    def test_wrong_release_history_nonpass_duplicate_and_empty_scopes_reject(self):
        for change in ({'release': '0.0.0'}, {'scope_kind': 'historical'}, {'status': 'FAILED'},
                       {'reviewed_file_sha256': {}}, {'review_id': ''}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                validate_current_reviews([{**self.record, **change}], self.files, VERSION)
        with self.assertRaises(ValidationError):
            validate_current_reviews([self.record, self.record], self.files, VERSION)
        with self.assertRaises(ValidationError):
            validate_current_reviews([], self.files, VERSION)

    def test_missing_file_or_required_coverage_reject(self):
        with self.assertRaisesRegex(ValidationError, 'bytes differ'):
            validate_current_reviews([self.record], {'README.md': self.files['README.md']}, VERSION)
        with self.assertRaisesRegex(ValidationError, 'coverage missing'):
            validate_current_reviews([self.record], self.files, VERSION, ['new-code.py'])

    def test_ambiguous_paths_and_invalid_digests_reject(self):
        for name in ('../README.md', '/README.md', 'dir\\README.md', 'C:/README.md', 'dir//README.md'):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                validate_current_reviews([{**self.record, 'reviewed_file_sha256': {name: '0' * 64}}], self.files, VERSION)
        with self.assertRaisesRegex(ValidationError, 'lowercase SHA-256'):
            validate_current_reviews([{**self.record, 'reviewed_file_sha256': {'README.md': 'A' * 64}}], self.files, VERSION)


class ShippedReviewEntryPointTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='awf-current-review-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        self.source=self.root/'source'; self.source.mkdir()
        self.files={'README.md':b'Reviewed guide\n','code.py':b'answer = 42\n'}
        self.record={'status':'PASS','scope_kind':'current_release','release':VERSION,
                     'review_id':'synthetic-unit-fixture','reviewed_file_sha256':{n:hashlib.sha256(b).hexdigest() for n,b in self.files.items()}}
        for name,raw in self.files.items():(self.source/name).write_bytes(raw)
        self.seal()
        self.reviews=self.root/'review.json'
        self.reviews.write_text(json.dumps(self.record),encoding='utf-8')
        self.review_pin=hashlib.sha256(self.reviews.read_bytes()).hexdigest()

    def seal(self):
        data={'format':'awf-manifest-1','template_version':VERSION,
              'files':{n:hashlib.sha256((self.source/n).read_bytes()).hexdigest() for n in self.files}}
        raw=json.dumps(data).encode('utf-8')
        (self.source/'MANIFEST.json').write_bytes(raw)
        self.manifest_pin=hashlib.sha256(raw).hexdigest()

    def command(self,extra=()):
        env={k:v for k,v in os.environ.items() if k.upper() not in ('PYTHONPATH','PYTHONHOME')}
        return subprocess.run([sys.executable,'-B',str(ROOT/'scripts/release_review.py'),
            '--source',str(self.source),'--expected-manifest-sha256',self.manifest_pin,
            '--reviews',str(self.reviews),'--expected-reviews-sha256',self.review_pin,*map(str,extra)],
            cwd=self.root,env=env,capture_output=True,timeout=30)

    def test_actual_shipped_cli_verifies_pins_and_defaults_to_all_content(self):
        done=self.command()
        self.assertEqual(0,done.returncode,done.stderr.decode())
        result=json.loads(done.stdout)
        self.assertEqual('all_manifest_content',result['coverage_basis'])
        self.assertEqual(['README.md','code.py'],result['required_paths'])
        self.assertTrue(result['release_qualified'])
        self.assertFalse(result['authenticates_reviewers'])

    def test_post_review_edit_even_after_manifest_reseal_fails(self):
        (self.source/'README.md').write_bytes(b'Changed after review\n')
        self.seal()
        done=self.command()
        self.assertEqual(2,done.returncode)
        self.assertIn('Current review bytes differ',done.stderr.decode())

    def test_wrong_pin_and_duplicate_keys_are_clean_cli_failures(self):
        for raw in (b'{}\n',b'{"status":"PASS","status":"PASS"}'):
            self.reviews.write_bytes(raw)
            if b'status' in raw:self.review_pin=hashlib.sha256(raw).hexdigest()
            done=self.command()
            self.assertEqual(2,done.returncode)
            self.assertNotIn('Traceback',done.stderr.decode())

    def test_scope_is_explicit_pinned_nonempty_and_existing(self):
        scope=self.root/'scope.json'
        for paths in ([],['missing.py'],['README.md','README.md']):
            scope.write_text(json.dumps({'format':'awf-review-coverage-1','release':VERSION,'paths':paths}),encoding='utf-8')
            done=self.command(['--review-required-paths',scope,'--expected-review-required-paths-sha256',hashlib.sha256(scope.read_bytes()).hexdigest()])
            self.assertEqual(2,done.returncode)
        scope.write_text(json.dumps({'format':'awf-review-coverage-1','release':VERSION,'paths':['README.md']}),encoding='utf-8')
        done=self.command(['--review-required-paths',scope,'--expected-review-required-paths-sha256',hashlib.sha256(scope.read_bytes()).hexdigest()])
        self.assertEqual(0,done.returncode,done.stderr.decode())
        self.assertEqual('pinned_operator_scope',json.loads(done.stdout)['coverage_basis'])

    def test_missing_review_and_partial_scope_pair_cannot_qualify(self):
        with self.assertRaisesRegex(ValidationError,'review evidence'):
            review_source(self.source,self.manifest_pin,None,None)
        with self.assertRaisesRegex(ValidationError,'both'):
            review_source(self.source,self.manifest_pin,self.reviews,self.review_pin,None,'0'*64)


if __name__ == '__main__':
    unittest.main()
