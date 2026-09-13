#!/usr/bin/env python3
"""Source-release entry point for the installed self-test runner."""
from pathlib import Path
import runpy
import sys
sys.dont_write_bytecode = True
if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parents[1] / '.agentic/scripts/self_test.py'), run_name='__main__')
