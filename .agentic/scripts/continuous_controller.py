#!/usr/bin/env python3
"""Durable production entry point for continuous stream decisions and digests."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".agentic/lib"))

import argparse
import json

from agentic.canonical import load
from agentic.continuous_controller import ContinuousControllerStore


def main(argv=None, default_root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--stream", action="append", dest="streams", required=True)
    parser.add_argument("--worktree-root", type=Path, action="append", required=True)
    parser.add_argument("--cadence-seconds", type=int)
    sub = parser.add_subparsers(dest="command", required=True)
    cycle = sub.add_parser("cycle")
    cycle.add_argument("--inventory", type=Path, required=True)
    cycle.add_argument("--now", required=True)
    cycle.add_argument("--host-capacity", type=int, required=True)
    finish = sub.add_parser("finish")
    finish.add_argument("--inventory", type=Path, required=True)
    finish.add_argument("--now", required=True)
    finish.add_argument("--host-capacity", type=int, required=True)
    finish.add_argument("--finished-stream", required=True)
    finish.add_argument("--ticket", required=True)
    digest = sub.add_parser("digest")
    digest.add_argument("--now", required=True)
    ack = sub.add_parser("ack")
    ack.add_argument("--delivery-id", required=True)
    ack.add_argument("--delivered-at", required=True)
    sub.add_parser("snapshot")
    args = parser.parse_args(argv)
    config_path = default_root / ".agentic/PROJECT_CONFIG.yaml"
    configured_cadence = load(config_path).get("controller", {}).get("status_cadence_seconds", 900)
    cadence_seconds = args.cadence_seconds if args.cadence_seconds is not None else configured_cadence
    if not isinstance(cadence_seconds, int) or isinstance(cadence_seconds, bool) or cadence_seconds < 1:
        parser.error("controller.status_cadence_seconds must be a positive integer")
    store = ContinuousControllerStore(args.state, args.streams, cadence_seconds,
                                      worktree_roots=args.worktree_root)
    if args.command == "cycle":
        streams = store.schedule(load(args.inventory), args.now, args.host_capacity)
        output = {"streams": streams, "pending_digest": store.digest(args.now),
                  "delivery_acknowledged": False, "execution_authority": False}
    elif args.command == "finish":
        streams = store.finish_and_refill(args.finished_stream, args.ticket, load(args.inventory),
                                          args.now, args.host_capacity)
        output = {"streams": streams, "pending_digest": store.digest(args.now),
                  "delivery_acknowledged": False, "execution_authority": False}
    elif args.command == "digest":
        output = {"pending_digest": store.digest(args.now), "delivery_acknowledged": False,
                  "execution_authority": False}
    elif args.command == "ack":
        output = {"digest": store.acknowledge_digest(args.delivery_id, args.delivered_at),
                  "delivery_acknowledged": True, "execution_authority": False}
    else:
        output = {"streams": store.snapshot(), "execution_authority": False}
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
