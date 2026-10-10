"""Read release approval outside the candidate project; never execute its helpers."""
from __future__ import annotations

import io
import os
from pathlib import Path
import re
import zipfile

from . import VERSION, ValidationError
from .canonical import loads, sha256
from .installer import MANIFEST, verify_release
from .safeio import Tree, relative_parts

LIMIT = 32 * 1024 * 1024
PIN = re.compile(r'[0-9a-f]{64}')


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def outside_project(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    require(not path.is_relative_to(root) and not root.is_relative_to(path),
            'Release approval source must be outside and separate from the candidate project')
    return path


def bundled_manifest(archive, expected):
    """Verify closure and bytes in a host-approved bundle without extracting it."""
    prefix = f'agentic-workflow-template-v{VERSION}/'
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        entries = z.infolist()
        require(0 < len(entries) <= 5000 and sum(e.file_size for e in entries) <= LIMIT,
                'Trusted skill source archive exceeds its inventory/byte limit')
        files = {}
        for entry in entries:
            require(not entry.flag_bits & 1 and entry.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                    'Encrypted or unsupported source archive compression')
            require(entry.filename.startswith(prefix), 'Unexpected source archive prefix')
            name = entry.filename[len(prefix):]
            relative_parts(name)
            require(not entry.is_dir() and name.casefold() not in {p.casefold() for p in files}
                    and entry.file_size <= 5 * 1024 * 1024, 'Unsafe or duplicate source archive entry')
            files[name] = z.read(entry)
    raw = files.get(MANIFEST, b'')
    require(sha256(raw) == expected, 'Trusted skill bundled manifest digest mismatch')
    manifest = loads(raw.decode('utf-8'))
    require(set(manifest) == {'format', 'template_version', 'files'}
            and manifest['format'] == 'awf-manifest-1' and manifest['template_version'] == VERSION,
            'Trusted skill bundled manifest header mismatch')
    require(set(files) - {MANIFEST, 'MANIFEST.md'} == set(manifest['files']),
            'Trusted skill source archive membership mismatch')
    require(all(sha256(files[p]) == digest for p, digest in manifest['files'].items()),
            'Trusted skill source archive content mismatch')
    return expected


def establish_release_trust(root, *, release_source=None, expected_manifest_sha256=None):
    """Verify one external source or the atomically installed host-skill receipt."""
    if release_source is not None or expected_manifest_sha256 is not None:
        require(release_source is not None and isinstance(expected_manifest_sha256, str)
                and PIN.fullmatch(expected_manifest_sha256),
                'Use --release-source with an independently approved --expected-manifest-sha256')
        source = outside_project(release_source, root)
        if source.is_file():
            require(source.stat().st_size <= LIMIT, 'Release source archive exceeds its byte limit')
            digest = bundled_manifest(source.read_bytes(), expected_manifest_sha256)
            kind = 'verified_release_zip_and_approved_pin'
        else:
            with Tree(source) as tree:
                digest, _ = verify_release(tree, expected_manifest_sha256)
            kind = 'verified_extracted_release_and_approved_pin'
        return {'manifest_sha256': digest, 'basis': kind,
                'release_source': str(source), 'git_provenance_required': False}
    codex_home = Path(os.environ.get('CODEX_HOME') or (Path.home() / '.codex')).expanduser().resolve()
    skill = outside_project(codex_home / 'skills' / 'awf', root)
    try:
        with Tree(skill) as tree:
            receipt = loads(tree.read('.awf-install-receipt.json', maximum=1024 * 1024).decode('utf-8'))
            require(receipt.get('format') == 'awf-host-skill-trust-1'
                    and receipt.get('schema_version') == 1
                    and receipt.get('inventory_complete') is True,
                    'Host skill trust receipt is not atomic/complete format 1')
            raw = tree.read('SKILL-MANIFEST.json', maximum=1024 * 1024)
            require(sha256(raw) == receipt.get('manifest_sha256'), 'Host skill installation receipt mismatch')
            manifest = loads(raw.decode('utf-8'))
            require(manifest.get('name') == receipt.get('name') == 'awf'
                    and manifest.get('version') == receipt.get('version') == VERSION,
                    'Trusted host AWF skill version does not match this installation')
            entries = manifest.get('files')
            require(isinstance(entries, list) and 0 < len(entries) <= 1000, 'Invalid host skill inventory')
            approved = {}
            for entry in entries:
                name = entry['path']
                relative_parts(name)
                require(name.casefold() not in {p.casefold() for p in approved}, 'Duplicate host skill path')
                approved[name] = entry['sha256']
            require(approved == receipt.get('files'), 'Host skill approved inventory differs from its receipt')
            local = receipt.get('preserved_local_files', [])
            require(isinstance(local, list) and all(isinstance(name, str) for name in local),
                    'Host skill local-file inventory is invalid')
            for name in local:
                relative_parts(name)
            local_inventory = receipt.get('preserved_local_inventory')
            require(isinstance(local_inventory, dict) and set(local_inventory) == set(local)
                    and all(isinstance(value, str) and PIN.fullmatch(value)
                            for value in local_inventory.values()),
                    'Host skill local-file digest inventory is invalid')
            expected_paths = set(approved) | set(local) | {
                'SKILL-MANIFEST.json', '.awf-install-receipt.json'}
            require(set(tree.file_list(reject_root_git=True)) == expected_paths,
                    'Host skill inventory is not closed by its receipt')
            total = 0
            content = {}
            for name, digest in approved.items():
                content[name] = tree.read(name, maximum=LIMIT)
                total += len(content[name])
                require(total <= LIMIT and sha256(content[name]) == digest, 'Host skill content/size mismatch: ' + name)
            for name, digest in local_inventory.items():
                raw = tree.read(name, maximum=LIMIT)
                total += len(raw)
                require(total <= LIMIT and sha256(raw) == digest,
                        'Host skill preserved local content/size mismatch: ' + name)
            metadata = loads(content['assets/release.json'].decode('utf-8'))
            require(metadata.get('format') == 'awf-bundled-release-1' and metadata.get('version') == VERSION,
                    'Trusted host bundled release metadata mismatch')
            name = metadata['archive']
            relative_parts(name)
            require('/' not in name, 'Trusted host archive name must be a basename')
            archive = content['assets/' + name]
            require(sha256(archive) == metadata['archive_sha256'], 'Trusted host archive digest mismatch')
            digest = bundled_manifest(archive, metadata['manifest_sha256'])
            return {'manifest_sha256': digest, 'basis': 'trusted_host_installed_awf_skill',
                    'codex_home': str(codex_home), 'host_skill_path': str(skill),
                    'host_receipt_path': str(skill / '.awf-install-receipt.json'),
                    'verified_inventory': len(approved) + len(local_inventory), 'atomic_receipt': True,
                    'git_provenance_required': False}
    except (ValidationError, OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError, zipfile.BadZipFile) as exc:
        detail = ': ' + str(exc) if isinstance(exc, ValidationError) else ''
        raise ValidationError('Verify/update the trusted host AWF skill or use status --release-source ABS_SOURCE '
                              '--expected-manifest-sha256 TRUSTED_PIN' + detail) from exc


def approved_manifest(root, *, release_source=None, expected_manifest_sha256=None):
    """Compatibility projection for callers that only need the digest and basis."""
    trust = establish_release_trust(root, release_source=release_source,
                                    expected_manifest_sha256=expected_manifest_sha256)
    basis = trust['basis']
    if basis in {'verified_release_zip_and_approved_pin',
                 'verified_extracted_release_and_approved_pin'}:
        basis = 'explicit_external_source_and_approved_pin'
    return trust['manifest_sha256'], basis
