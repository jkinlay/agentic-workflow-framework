"""Reproduce clearly synthetic project progress/action examples."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    examples={
        'project-status.json':{'state':'WAITING','summary':'Synthetic example: CI for the assigned ticket is running.',
            'action':'Observe current-head CI and continue the next independent, dependency-ready stream ticket.',
            'owner':'controller','trigger':'next CI observation or ready stream dispatch','authorization':None},
        'routine-action.json':{'kind':'delegate','target':'synthetic independent Stream B ticket',
            'scope_authorized':True,'already_authorized':False,'platform_permitted':True,'prerequisites_met':True},
    }
    for name,value in examples.items():
        (ROOT/'.agentic/examples'/name).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8',newline='\n')


if __name__=='__main__':
    main()
