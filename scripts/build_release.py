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
from agentic.installer import RELEASE_EXCLUDED_PREFIXES, release_member, verify_release
from agentic.safeio import Tree
from release_hygiene import check_release
from release_modes import archive_mode, load_modes


def archive_time():
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH', '315532800'))
    value = datetime.fromtimestamp(max(epoch, 315532800), timezone.utc)
    return (value.year, value.month, value.day, value.hour, value.minute, value.second // 2 * 2)


def release_paths(tree):
    """List release members without traversing governed excluded directories."""
    return [path for path in tree.file_list(exclude_root_git=True,
                                             exclude_prefixes=RELEASE_EXCLUDED_PREFIXES)
            if release_member(path)]


def manifest():
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
            data = tree.read(path)
            try:
                data.decode('utf-8')
            except UnicodeDecodeError as exc:
                raise ValidationError(f'Release text is not UTF-8: {path}') from exc
            if b'\r' in data or data.startswith(b'\xef\xbb\xbf'):
                raise ValidationError(f'Release text must use LF without a BOM: {path}; restore approved bytes before building')
            if path in paths:
                files[path] = sha256(data)
    value = {'format':'awf-manifest-1','template_version':VERSION,'files':files}
    raw = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('utf-8')
    (ROOT / 'MANIFEST.json').write_bytes(raw)
    lines = [f'# Release manifest — {VERSION}', '',
        f'{len(files)} content files are SHA-256 listed in MANIFEST.json. The ZIP additionally contains MANIFEST.json and this advisory inventory.', '',
        f'MANIFEST.json SHA-256: `{sha256(raw)}`', '',
        'The machine manifest excludes itself and this human-readable file to avoid recursive hashing. An independently approved manifest/ZIP digest establishes the expected bytes; these files are not a publisher signature.', '',
        '| File | SHA-256 |', '| --- | --- |']
    lines.extend(f'| `{p}` | `{h}` |' for p,h in files.items())
    (ROOT / 'MANIFEST.md').write_text('\n'.join(lines)+'\n', encoding='utf-8', newline='\n')
    with Tree(ROOT) as tree:
        verify_release(tree, sha256(raw), allow_source_checkout=True)
    return sha256(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--manifest-only', action='store_true')
    parser.add_argument('--git-mode-manifest', type=Path,
                        help='raw tagged-tree mode map (required for a materialized source without .git)')
    args = parser.parse_args()
    if not args.manifest_only and not args.output:
        parser.error('--output ZIP is required unless --manifest-only is used')
    if args.output and args.output.absolute().is_relative_to(ROOT):
        parser.error('ZIP output must be outside the release source tree')
    digest = manifest()
    if args.manifest_only:
        print(json.dumps({'manifest_sha256':digest}))
        return 0
    output = args.output.absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    modes = load_modes(ROOT, args.git_mode_manifest)
    # A materialized source can contain working-tree additions that were not
    # present in the raw Git mode manifest used to reproduce its base tree.
    # New regular files are conservatively archived non-executable; a live
    # checkout without an explicit manifest remains strict via load_modes().
    if args.git_mode_manifest:
        with Tree(ROOT) as tree:
            for relative in release_paths(tree):
                modes.setdefault(relative.replace('\\', '/'), '100644')
    prefix = 'agentic-workflow-template-v' + VERSION.removesuffix('.0') + '/'
    expected = {}
    with Tree(ROOT) as tree, zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for relative in release_paths(tree):
            data = tree.read(relative)
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
