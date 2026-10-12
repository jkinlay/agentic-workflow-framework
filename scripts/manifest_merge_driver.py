#!/usr/bin/env python3
"""Deterministically merge AWF's derived manifests during PR synchronization."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
from agentic.child_process import child_env  # noqa: E402


MISSING = object()
ROW = re.compile(r"^\| `(?P<path>.+)` \| `(?P<digest>[0-9a-f]{64})` \|$")
TITLE = re.compile(r"^# Release manifest — (?P<version>\d+\.\d+\.\d+)$")
MERGE_IDENTITY_CONFIG = [
    "-c",
    "user.name=AWF Manifest Merge",
    "-c",
    "user.email=awf-manifest@example.invalid",
]


def merge_value(base, ours, theirs):
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    # Manifests are derived. Keep the current side for a same-entry conflict;
    # the code conflict and the manifest check require regeneration afterward.
    return ours


def merge_metadata(base, ours, theirs):
    if ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    raise ValueError('manifest metadata changed incompatibly')


def merge_mapping(base, ours, theirs):
    merged = {}
    for key in sorted(set(base) | set(ours) | set(theirs)):
        value = merge_value(base.get(key, MISSING), ours.get(key, MISSING),
                            theirs.get(key, MISSING))
        if value is not MISSING:
            merged[key] = value
    return merged


def load_machine(path):
    def unique_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate machine manifest key')
            value[key] = item
        return value

    value = json.loads(Path(path).read_bytes(), object_pairs_hook=unique_object)
    if set(value) != {'format', 'template_version', 'files'}:
        raise ValueError('unsupported machine manifest fields')
    if value['format'] != 'awf-manifest-1' or not isinstance(value['files'], dict):
        raise ValueError('unsupported machine manifest format')
    for name, digest in value['files'].items():
        if not isinstance(name, str) or re.fullmatch(r'[0-9a-f]{64}', digest or '') is None:
            raise ValueError('invalid machine manifest entry')
    return value


def render_machine(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('utf-8')


def load_advisory(path):
    version = None
    files = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        title = TITLE.fullmatch(line)
        if title:
            version = title.group('version')
        row = ROW.fullmatch(line)
        if row:
            name = row.group('path')
            if name in files:
                raise ValueError('duplicate advisory manifest entry')
            files[name] = row.group('digest')
    if version is None:
        raise ValueError('advisory manifest title is missing')
    return version, files


def render_advisory(version, files):
    lines = [f'# Release manifest — {version}', '',
        'Each content file is SHA-256 listed on its own line in MANIFEST.json and below. The ZIP additionally contains MANIFEST.json and this advisory inventory.', '',
        'The machine manifest excludes itself and this human-readable file to avoid recursive hashing. An independently approved manifest/ZIP digest establishes the expected bytes; these files are not a publisher signature.', '',
        '| File | SHA-256 |', '| --- | --- |']
    lines.extend(f'| `{name}` | `{digest}` |' for name, digest in sorted(files.items()))
    return ('\n'.join(lines) + '\n').encode('utf-8')


def merge_machine(base_path, ours_path, theirs_path):
    base, ours, theirs = map(load_machine, (base_path, ours_path, theirs_path))
    value = {
        'format': merge_metadata(base['format'], ours['format'], theirs['format']),
        'template_version': merge_metadata(base['template_version'], ours['template_version'],
                                           theirs['template_version']),
        'files': merge_mapping(base['files'], ours['files'], theirs['files']),
    }
    if value['format'] != 'awf-manifest-1':
        raise ValueError('merged machine manifest format is unsupported')
    return render_machine(value)


def merge_advisory(base_path, ours_path, theirs_path):
    base_version, base = load_advisory(base_path)
    ours_version, ours = load_advisory(ours_path)
    theirs_version, theirs = load_advisory(theirs_path)
    version = merge_metadata(base_version, ours_version, theirs_version)
    return render_advisory(version, merge_mapping(base, ours, theirs))


def driver_command():
    """Return the command Git invokes for one manifest merge."""
    arguments = [sys.executable, "-B", str(Path(__file__).resolve())]
    if os.name == "nt":
        command = f'"{arguments[0]}" -B "{arguments[2]}"'
    else:
        command = " ".join(shlex.quote(argument) for argument in arguments)
    return command + " %O %A %B %P"


def merge_ref(reference):
    """Rehearse a sync while registering the driver for this Git invocation."""
    completed = subprocess.run(
        [
            "git",
            *MERGE_IDENTITY_CONFIG,
            "-c",
            "merge.awf-manifest.name=AWF manifest merge driver",
            "-c",
            f"merge.awf-manifest.driver={driver_command()}",
            "merge",
            "--no-commit",
            "--no-ff",
            "--",
            reference,
        ],
        check=False,
        env=child_env(),
    )
    return completed.returncode


def sync_ref(reference, push_branch):
    """Merge a base ref with the driver and push the resulting PR head."""
    merged = subprocess.run(
        [
            "git",
            *MERGE_IDENTITY_CONFIG,
            "-c",
            "merge.awf-manifest.name=AWF manifest merge driver",
            "-c",
            f"merge.awf-manifest.driver={driver_command()}",
            "merge",
            "--no-edit",
            "--no-ff",
            "--",
            reference,
        ],
        check=False,
        env=child_env(),
    )
    if merged.returncode:
        return merged.returncode
    pushed = subprocess.run(
        ["git", "push", "origin", "--", f"HEAD:refs/heads/{push_branch}"],
        check=False,
        env=child_env(),
    )
    return pushed.returncode


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) == 2 and arguments[0] == "--merge-ref":
        return merge_ref(arguments[1])
    if (len(arguments) == 4 and arguments[0] == "--sync-ref"
            and arguments[2] == "--push-branch"):
        return sync_ref(arguments[1], arguments[3])
    if len(arguments) != 4:
        print(
            "usage: manifest_merge_driver.py --merge-ref REF\n"
            "   or: manifest_merge_driver.py --sync-ref REF --push-branch BRANCH\n"
            "   or: manifest_merge_driver.py BASE OURS THEIRS PATH",
            file=sys.stderr,
        )
        return 2
    base, ours, theirs, relative = arguments
    try:
        normalized = relative.replace('\\', '/')
        if normalized == 'MANIFEST.json':
            merged = merge_machine(base, ours, theirs)
        elif normalized == 'MANIFEST.md':
            merged = merge_advisory(base, ours, theirs)
        else:
            raise ValueError('driver is restricted to AWF manifest paths')
        Path(ours).write_bytes(merged)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f'AWF manifest merge failed: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
