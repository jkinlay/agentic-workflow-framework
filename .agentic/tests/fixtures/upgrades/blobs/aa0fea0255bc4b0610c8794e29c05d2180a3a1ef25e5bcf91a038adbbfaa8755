"""Independent host/source trust fixtures; no network or release code execution."""
from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agentic import VERSION, ValidationError
from agentic.canonical import sha256
from agentic.installer import json_bytes
from agentic.release_trust import approved_manifest, bundled_manifest


class ReleaseTrustTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='awf-release-trust-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.project = self.base / 'project'
        self.project.mkdir()
        self.source = self.base / 'approved'
        self.source.mkdir()
        self.manifest = {'format': 'awf-manifest-1', 'template_version': VERSION,
                         'files': {'AGENTS.md': sha256(b'# Synthetic approved release\n')}}
        self.raw = json_bytes(self.manifest)
        self.pin = sha256(self.raw)
        self.files = {'MANIFEST.json': self.raw, 'AGENTS.md': b'# Synthetic approved release\n'}
        for name, raw in self.files.items():
            (self.source / name).write_bytes(raw)
        self.skill = self.base / 'host/skills/awf'
        (self.skill / 'assets').mkdir(parents=True)
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z:
            for name, raw in self.files.items():
                z.writestr(f'agentic-workflow-template-v{VERSION}/' + name, raw)
        self.archive = data.getvalue()
        archive_name = f'agentic-workflow-template-v{VERSION}.zip'
        metadata = {'format': 'awf-bundled-release-1', 'version': VERSION, 'archive': archive_name,
                    'archive_sha256': sha256(self.archive), 'manifest_sha256': self.pin}
        content = {'assets/' + archive_name: self.archive, 'assets/release.json': json_bytes(metadata)}
        self.skill_map = {name: sha256(raw) for name, raw in content.items()}
        manifest = json_bytes({'name': 'awf', 'version': VERSION,
                              'files': [{'path': name, 'sha256': pin} for name, pin in self.skill_map.items()]})
        self.receipt = {'name': 'awf', 'version': VERSION, 'manifest_sha256': sha256(manifest), 'files': self.skill_map}
        for name, raw in content.items():
            (self.skill / name).write_bytes(raw)
        (self.skill / 'SKILL-MANIFEST.json').write_bytes(manifest)
        (self.skill / '.awf-install-receipt.json').write_bytes(json_bytes(self.receipt))

    def host(self):
        with patch.dict(os.environ, {'CODEX_HOME': str(self.base / 'host')}):
            return approved_manifest(self.project)

    def test_external_complete_source_and_independent_pin(self):
        self.assertEqual(approved_manifest(self.project, release_source=self.source,
                         expected_manifest_sha256=self.pin), (self.pin, 'explicit_external_source_and_approved_pin'))

    def test_host_skill_receipt_closure_and_bundled_source(self):
        self.assertEqual(self.host(), (self.pin, 'trusted_host_installed_awf_skill'))

    def test_host_bundle_byte_edit_is_rejected(self):
        (self.skill / 'assets/release.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValidationError, 'content/size mismatch'):
            self.host()

    def test_host_approved_inventory_disagreement_is_rejected(self):
        self.receipt['files'] = {}
        (self.skill / '.awf-install-receipt.json').write_bytes(json_bytes(self.receipt))
        with self.assertRaisesRegex(ValidationError, 'inventory differs'):
            self.host()

    def test_old_host_version_has_actionable_next_step(self):
        self.receipt['version'] = '0.0.1'
        (self.skill / '.awf-install-receipt.json').write_bytes(json_bytes(self.receipt))
        with self.assertRaisesRegex(ValidationError, '--release-source'):
            self.host()

    def test_candidate_local_skill_cannot_supply_trust(self):
        with patch.dict(os.environ, {'CODEX_HOME': str(self.project / '.codex')}):
            with self.assertRaisesRegex(ValidationError, 'outside and separate'):
                approved_manifest(self.project)

    def test_extra_source_content_is_rejected_even_with_matching_manifest_pin(self):
        (self.source / 'unapproved.py').write_bytes(b'# inert extra\n')
        with self.assertRaisesRegex(ValidationError, 'membership mismatch'):
            approved_manifest(self.project, release_source=self.source, expected_manifest_sha256=self.pin)

    def test_archive_self_declared_digest_cannot_replace_host_pin(self):
        with self.assertRaisesRegex(ValidationError, 'manifest digest mismatch'):
            bundled_manifest(self.archive, '0' * 64)

    def test_unsupported_archive_compression_is_a_typed_refusal(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_BZIP2) as z:
            for name, raw in self.files.items():
                z.writestr(f'agentic-workflow-template-v{VERSION}/' + name, raw)
        with self.assertRaisesRegex(ValidationError, 'unsupported source archive compression'):
            bundled_manifest(data.getvalue(), self.pin)


if __name__ == '__main__':
    unittest.main()
