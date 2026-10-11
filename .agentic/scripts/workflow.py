#!/usr/bin/env python3
"""Portable source/installed workflow reference CLI."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))
try:
    from agentic.cli import main
except ImportError as exc:
    print(f"Missing dependency: {exc}. Install .agentic/requirements.lock with --require-hashes.", file=sys.stderr)
    raise SystemExit(3)
if __name__ == "__main__":
    # Keep the project-owned operating interface separate from governance CLI.
    import argparse
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument("--root", type=Path, default=ROOT)
    probe.add_argument("command", nargs="?")
    options, remaining = probe.parse_known_args()
    if options.command == "operating":
        from agentic.operating import main as operating_main
        raise SystemExit(operating_main(remaining, default_root=options.root))
    if options.command == "controller":
        from continuous_controller import main as controller_main
        raise SystemExit(controller_main(remaining, default_root=options.root))
    if options.command == "review-completion":
        from review_completion import main as review_completion_main
        raise SystemExit(review_completion_main(remaining, default_root=options.root))
    if options.command == "candidate-id":
        import argparse
        from agentic import ValidationError
        from agentic.canonical import candidate_id, loads

        parser = argparse.ArgumentParser(
            prog="workflow.py candidate-id",
            description="Print the AWF candidate fingerprint for a JSON candidate object.",
            epilog=(
                "Binding fingerprints use lowercase SHA-256 over "
                "AWF-C14N-1\\0<domain>\\0<canonical JSON>. The bindings are: "
                "candidate_id = fingerprint('candidate', candidate); "
                "requirements_hash = fingerprint('requirements', snapshot); "
                "contract_hash = fingerprint('contract', contract); and "
                "policy_hash = fingerprint('policy', {'config': config, 'workflow': workflow}). "
                "Canonical JSON rejects floats and duplicate keys, preserves string codepoints, "
                "sorts object keys, and uses compact UTF-8 JSON. Example: the candidate {} "
                "produces f6ad6529bb10204be62fc7b66e03cb77c1a1be8f73ce4471767659efcef8bb40."
            ),
        )
        parser.add_argument("candidate", nargs="?", default="-",
                            help="JSON file path, or '-' / omitted to read stdin")
        args = parser.parse_args(remaining)
        try:
            text = sys.stdin.read() if args.candidate == "-" else Path(args.candidate).read_text(encoding="utf-8")
            print(candidate_id(loads(text)))
        except (OSError, UnicodeError, ValidationError) as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(2)
        raise SystemExit(0)
    raise SystemExit(main(default_root=options.root))
