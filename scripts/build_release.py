#!/usr/bin/env python3
"""Generate a complete manifest and deterministic source ZIP; run tests separately."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import zipfile
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import ValidationError, VERSION
from agentic.canonical import sha256
from agentic.installer import (CONFIG, PROVENANCE, RELEASE_EXCLUDED_PREFIXES,
                               SOURCE_CONFIG_PATHS, SOURCE_CONFIG_PREFIX,
                               install_owned_path, release_member)
from agentic.safeio import Tree
from release_hygiene import check_release
from release_modes import archive_mode, load_modes


SOURCE_CONFIGS = {
    CONFIG: SOURCE_CONFIG_PREFIX + "PROJECT_CONFIG.yaml",
    PROVENANCE: SOURCE_CONFIG_PREFIX + "workflow-version.yaml",
    "OPERATING_CONFIG.yaml": SOURCE_CONFIG_PREFIX + "OPERATING_CONFIG.yaml",
}


def archive_time():
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', '315532800'))
    value = datetime.fromtimestamp(max(epoch, 315532800), timezone.utc)
    return (value.year, value.month, value.day, value.hour, value.minute, value.second // 2 * 2)


def release_paths(tree):
    """List logical release members without reading install-owned source state."""
    if set(SOURCE_CONFIGS) != set(SOURCE_CONFIG_PATHS):
        raise ValidationError('Release source configuration mapping differs from installer-owned configuration')
    paths = {path for path in tree.file_list(exclude_root_git=True,
                                             exclude_prefixes=RELEASE_EXCLUDED_PREFIXES)
             if release_member(path) and not install_owned_path(path)}
    if hasattr(tree, 'inspect'):
        missing = [source for source in SOURCE_CONFIGS.values() if tree.inspect(source) is None]
        if missing:
            raise ValidationError('Release source configuration is incomplete: ' + ', '.join(missing))
        paths.update(SOURCE_CONFIGS)
    return sorted(paths)


def release_bytes(tree, path):
    """Read one logical member, substituting the immutable source template."""
    return tree.read(SOURCE_CONFIGS.get(path, path))


def verify_virtual_release(tree, manifest_raw):
    """Verify the logical source view that will be materialized into the ZIP."""
    value = json.loads(manifest_raw)
    expected = set(release_paths(tree)) - {'MANIFEST.json', 'MANIFEST.md'}
    if set(value.get('files', {})) != expected:
        raise ValidationError('Virtual release manifest file membership mismatch')
    folded = [path.casefold() for path in expected]
    if len(set(folded)) != len(folded):
        raise ValidationError('Case-colliding paths in virtual release')
    for path, digest in value['files'].items():
        if sha256(release_bytes(tree, path)) != digest:
            raise ValidationError(f'Virtual release digest mismatch: {path}')


def render_manifests(files, version=VERSION):
    """Render stable manifests whose only content-dependent lines are per-file entries."""
    files = dict(sorted(files.items()))
    value = {'format': 'awf-manifest-1', 'template_version': version, 'files': files}
    machine = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('utf-8')
    lines = [f'# Release manifest — {version}', '',
        'Each content file is SHA-256 listed on its own line in MANIFEST.json and below. The ZIP additionally contains MANIFEST.json and this advisory inventory.', '',
        'The machine manifest excludes itself and this human-readable file to avoid recursive hashing. An independently approved manifest/ZIP digest establishes the expected bytes; these files are not a publisher signature.', '',
        '| File | SHA-256 |', '| --- | --- |']
    lines.extend(f'| `{path}` | `{digest}` |' for path, digest in files.items())
    advisory = ('\n'.join(lines) + '\n').encode('utf-8')
    return machine, advisory


def write_manifest_files(root, machine, advisory):
    """Replace both generated files only after their complete bytes are known."""
    root = Path(root)
    (root / 'MANIFEST.json').write_bytes(machine)
    (root / 'MANIFEST.md').write_bytes(advisory)


def check_manifest_files(root, machine, advisory):
    """Fail closed on missing or hand-edited generated files without rewriting them."""
    root = Path(root)
    expected = {'MANIFEST.json': machine, 'MANIFEST.md': advisory}
    missing = [name for name in expected if not (root / name).is_file()]
    if missing:
        raise ValidationError('Release manifest files are missing: ' + ', '.join(missing))
    stale = [name for name, data in expected.items() if (root / name).read_bytes() != data]
    if stale:
        raise ValidationError('Release manifest files are stale or hand-edited: ' + ', '.join(stale))


def manifest(check=False):
    check_release(ROOT)
    with Tree(ROOT) as tree:
        all_paths = release_paths(tree)
        paths = [p for p in all_paths if p not in {'MANIFEST.json','MANIFEST.md'}]
        forbidden = {'.git', '.tmp', 'tmp', '__pycache__', '.venv', 'venv', '.pytest_cache', '.agentic-install', '.agentic-backup'}
        if any(set(Path(p).parts) & forbidden or p.endswith(('.pyc', '.tmp', '.zip')) for p in paths):
            raise ValidationError('Source contains runtime/build residue; review it before packaging')
        files = {}
        # Do not bless checkout-induced EOL changes by simply rehashing them.
        # This release consists exclusively of UTF-8 text, including manifests.
        for path in all_paths:
            data = release_bytes(tree, path)
            try:
                data.decode('utf-8')
            except UnicodeDecodeError as exc:
                raise ValidationError(f'Release text is not UTF-8: {path}') from exc
            if b'\r' in data or data.startswith(b'\xef\xbb\xbf'):
                raise ValidationError(f'Release text must use LF without a BOM: {path}; restore approved bytes before building')
            if path in paths:
                files[path] = sha256(data)
    raw, advisory = render_manifests(files)
    if check:
        check_manifest_files(ROOT, raw, advisory)
    else:
        write_manifest_files(ROOT, raw, advisory)
    with Tree(ROOT) as tree:
        verify_virtual_release(tree, raw)
    return sha256(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--manifest-only', action='store_true')
    action.add_argument('--check-manifest', action='store_true',
                        help='verify committed manifest bytes without rewriting them')
    parser.add_argument('--git-mode-manifest', type=Path,
                        help='raw tagged-tree mode map (required for a materialized source without .git)')
    args = parser.parse_args()
    if not (args.manifest_only or args.check_manifest) and not args.output:
        parser.error('--output ZIP is required unless a manifest action is used')
    if args.check_manifest and args.output:
        parser.error('--check-manifest cannot be combined with --output')
    if args.output and args.output.absolute().is_relative_to(ROOT):
        parser.error('ZIP output must be outside the release source tree')
    digest = manifest(check=args.check_manifest)
    if args.manifest_only or args.check_manifest:
        print(json.dumps({'manifest_sha256':digest}))
        return 0
    output = args.output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    modes = load_modes(ROOT, args.git_mode_manifest)
    # Every archive member must have an authoritative raw-Git mode.  Never
    # silently manufacture a mode for a missing manifest entry: that can hide
    # an incomplete source projection or strip an approved executable bit.
    prefix = 'agentic-workflow-template-v' + VERSION.removesuffix('.0') + '/'
    expected = {}
    with Tree(ROOT) as tree, zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for relative in release_paths(tree):
            data = release_bytes(tree, relative)
            info = zipfile.ZipInfo(prefix + relative.replace('\\', '/'), date_time=archive_time())
            info.create_system = 3
            info.external_attr = archive_mode(modes, relative.replace('\\', '/')) << 16
            info.compress_type = zipfile.ZIP_STORED
            info.extra = b''
            info.comment = b''
            archive.writestr(info, data, compress_type=zipfile.ZIP_STORED)
            expected[info.filename] = sha256(data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(expected) or len(archive.namelist()) != len(expected):
            raise ValidationError('ZIP membership/CRC failure')
        if any(sha256(archive.read(p)) != digest for p,digest in expected.items()):
            raise ValidationError('ZIP content mismatch')
    zip_digest = sha256(output.read_bytes())
    output.with_suffix('.zip.sha256').write_text(
        f'{zip_digest}  {output.name}\n', encoding='utf-8', newline='\n')
    output.with_suffix('.manifest.sha256').write_text(
        f'{digest}  MANIFEST.json\n', encoding='utf-8', newline='\n')
    print(json.dumps({'version':VERSION,'zip':str(output),'zip_sha256':zip_digest,'manifest_sha256':digest,
        'files':len(expected),'bytes':output.stat().st_size,'tests_run_by_builder':False}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
