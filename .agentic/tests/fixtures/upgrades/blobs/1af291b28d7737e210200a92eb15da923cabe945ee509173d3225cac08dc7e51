#!/usr/bin/env python3
"""Produce a clear project next step, authorization decision or exact gate handoff."""
import argparse
import json
from pathlib import Path
import sys
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.agentic/lib'))
from agentic.canonical import load, now_text
from agentic.contracts import Contracts
from agentic.installer import verify_installed
from agentic.interaction import decide_action, gate_handoff, render_markdown, status_report, rejected_next_step
from agentic.continuation import project_cycle, render_cycle


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--format',choices=['markdown','json'],default='markdown')
    sub=parser.add_subparsers(dest='command',required=True)
    status=sub.add_parser('report',help='Render a project status record with an explicit next action')
    status.add_argument('file',type=Path)
    cycle=sub.add_parser('cycle',help='Report every stream and PR, prepare required drafts, and derive ongoing project actions')
    cycle.add_argument('file',type=Path)
    cycle.add_argument('--now',default=None,help='Test clock for explicitly synthetic observations only')
    action=sub.add_parser('action',help='Classify a proposed action using previously verified coordinator facts')
    action.add_argument('file',type=Path)
    gate=sub.add_parser('gate-handoff',help='Render the exact existing request matching a fresh complete gate')
    for name in ['gate','request','config']:
        gate.add_argument('--'+name,type=Path,required=True)
    gate.add_argument('--now',default=None)
    args=parser.parse_args(argv)
    try:
        verify_installed(ROOT)
        if args.command=='report':
            data=load(args.file)
            required={'state','summary','action','owner','trigger','authorization'}
            if not isinstance(data,dict) or set(data)!=required:
                raise ValueError('Status input requires exactly state, summary, action, owner, trigger and authorization')
            result=status_report(**data)
        elif args.command=='cycle':
            data=load(args.file)
            if args.now and data.get('source') != 'synthetic_fixture':
                raise ValueError('A live project cycle uses the actual clock; --now is for synthetic observations only')
            result=project_cycle(data,Contracts(ROOT/'.agentic/schemas'),args.now or now_text())
        elif args.command=='action':
            data=load(args.file)
            allowed={'kind','target','scope_authorized','already_authorized','platform_permitted','prerequisites_met','authorization','platform_approval'}
            if not isinstance(data,dict) or not {'kind','target','scope_authorized'} <= set(data) or not set(data)<=allowed:
                raise ValueError('Invalid action decision fields')
            result=decide_action(**data)
        else:
            result=gate_handoff(load(args.gate),load(args.request),load(args.config),Contracts(ROOT/'.agentic/schemas'),args.now or now_text())
        print(json.dumps(result,indent=2) if args.format=='json' else render_cycle(result) if args.command=='cycle' else render_markdown(result),end='\n')
        return 0
    except Exception as exc:
        reason=f'{type(exc).__name__}: {exc}'
        result={'status':'REJECTED','reason':reason,'execution_authority':False,'next_step':rejected_next_step(reason)}
        print(json.dumps(result,indent=2) if args.format=='json' else render_markdown(result),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
