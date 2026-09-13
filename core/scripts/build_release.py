#!/usr/bin/env python3
"""Generate a complete manifest and deterministic source ZIP; run tests separately."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import zipfile
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import ValidationError, VERSION
from agentic.canonical import sha256
from agentic.installer import verify_release
from agentic.safeio import Tree
from release_hygiene import check_release


def manifest():
    check_release(ROOT)
    with Tree(ROOT) as tree:
        all_paths = tree.file_list(exclude_root_git=True)
        paths = [p for p in all_paths if p not in {'MANIFEST.json','MANIFEST.md'}]
        forbidden = {'.git', '__pycache__', '.venv', 'venv', '.pytest_cache', '.agentic-install', '.agentic-backup'}
        if any(set(Path(p).parts) & forbidden or p.endswith(('.pyc','.zip')) for p in paths):
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
        verify_release(tree, sha256(raw))
    return sha256(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--manifest-only', action='store_true')
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
    prefix = 'agentic-workflow-template-v' + '.'.join(VERSION.split('.')[:2]) + '/'
    expected = {}
    with Tree(ROOT) as tree, zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in tree.file_list(exclude_root_git=True):
            data = tree.read(relative)
            info = zipfile.ZipInfo(prefix + relative, date_time=(2026,9,11,0,0,0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            expected[info.filename] = sha256(data)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None or set(archive.namelist()) != set(expected) or len(archive.namelist()) != len(expected):
            raise ValidationError('ZIP membership/CRC failure')
        if any(sha256(archive.read(p)) != digest for p,digest in expected.items()):
            raise ValidationError('ZIP content mismatch')
    zip_digest = sha256(output.read_bytes())
    output.with_suffix('.zip.sha256').write_text(f'{zip_digest}  {output.name}\n', encoding='utf-8')
    output.with_suffix('.manifest.sha256').write_text(f'{digest}  MANIFEST.json\n', encoding='utf-8')
    print(json.dumps({'version':VERSION,'zip':str(output),'zip_sha256':zip_digest,'manifest_sha256':digest,
        'files':len(expected),'bytes':output.stat().st_size,'tests_run_by_builder':False}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
