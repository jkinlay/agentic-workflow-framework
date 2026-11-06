"""Unittest discovery with a disposable host home for each synthetic project.

Legacy routing fixtures reuse project IDs between test cases. Their protected
lifetime state must be isolated between cases, while remaining intact throughout
each case (including subprocesses/restarts). No production admission is replaced.
"""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest


class IsolatedHostResult(unittest.TextTestResult):
    def startTest(self, test):
        self.state = tempfile.TemporaryDirectory(prefix='awf-validation-')
        self.prior = os.environ.get('AWF_STATE_HOME')
        os.environ['AWF_STATE_HOME'] = self.state.name
        super().startTest(test)

    def stopTest(self, test):
        try:
            super().stopTest(test)
        finally:
            if self.prior is None:
                os.environ.pop('AWF_STATE_HOME', None)
            else:
                os.environ['AWF_STATE_HOME'] = self.prior
            self.state.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pattern')
    args = parser.parse_args()
    tests = str(Path(__file__).resolve().parent)
    suite = unittest.defaultTestLoader.discover(tests, pattern=args.pattern, top_level_dir=tests)
    result = unittest.TextTestRunner(resultclass=IsolatedHostResult).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
