"""Standard-library-only entry point for restoring a clone's ignored runtime."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from . import ValidationError


def main(argv=None, *, root):
    parser = argparse.ArgumentParser(prog='workflow.py provision-runtime')
    parser.add_argument('--wheelhouse', type=Path, required=True,
                        help='Absolute directory containing only hash-locked wheels')
    args = parser.parse_args(argv)
    try:
        if not args.wheelhouse.is_absolute():
            raise ValidationError('--wheelhouse must be an absolute path')
        from .adoption_config import ensure_installed_runtime
        result = ensure_installed_runtime(root, wheelhouse=args.wheelhouse, runtime_only=True)
    except (ValidationError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        for note in getattr(exc, '__notes__', ()):
            print(note, file=sys.stderr)
        return 2
    print('Interpreter: ' + result['interpreter'])
    print('Entry point: ' + result['entry_point'])
    from .runtime_commands import runtime_backup_warning
    for row in result['transaction_cleanup_failures']:
        print(runtime_backup_warning(row['path']), file=sys.stderr)
    return 0
