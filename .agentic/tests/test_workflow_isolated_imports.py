"""Exercise sibling dispatch with the documented isolated installed invocation."""
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.agentic/scripts/workflow.py'
COMMANDS = {'controller': 'continuous_controller', 'review-completion': 'review_completion'}

# Observe the real dispatched function without replacing its implementation or
# adding import paths in the probe. Help still runs through the actual parser.
DISPATCH_PROBE = r'''
import json, runpy, sys
from pathlib import Path
baseline = list(sys.path)
script, project, command, module = sys.argv[1:]
entry = (Path(script).resolve().parent / (module + '.py')).resolve()
def observe(frame, event, arg):
    if event == 'call' and frame.f_code.co_name == 'main' and Path(frame.f_code.co_filename).resolve() == entry:
        print('AWF_DISPATCH=' + json.dumps(dict(
            baseline=baseline, paths=list(sys.path), isolated=sys.flags.isolated,
            no_bytecode=sys.dont_write_bytecode, origin=frame.f_code.co_filename,
            default_root=str(frame.f_locals['default_root']))))
        sys.setprofile(None)
sys.setprofile(observe)
sys.argv = [script, '--root', project, command, '--help']
runpy.run_path(script, run_name='__main__')
'''


class WorkflowIsolatedImportsTests(unittest.TestCase):
    def help_command(self, command):
        with tempfile.TemporaryDirectory(prefix='awf-workflow-cwd-') as raw:
            result = subprocess.run(
                [sys.executable, '-B', '-I', str(WORKFLOW), '--root', str(ROOT), command, '--help'],
                cwd=raw, capture_output=True, text=True, timeout=30, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('usage:', result.stdout)
        self.assertIn('--state', result.stdout)

    def test_controller_help_under_isolated_runtime(self):
        self.help_command('controller')

    def test_review_completion_help_under_isolated_runtime(self):
        self.help_command('review-completion')

    def test_dispatch_adds_only_installed_scripts_and_preserves_isolation(self):
        for command, module in COMMANDS.items():
            with self.subTest(command=command), tempfile.TemporaryDirectory(prefix='awf-workflow-cwd-') as raw:
                cwd = Path(raw).resolve()
                # A same-named module beside the caller must never be imported.
                for name in COMMANDS.values():
                    (cwd / (name + '.py')).write_text("raise RuntimeError('CWD imported')\n", encoding='utf-8')
                result = subprocess.run(
                    [sys.executable, '-B', '-I', '-c', DISPATCH_PROBE,
                     str(WORKFLOW), str(cwd), command, module],
                    cwd=cwd, capture_output=True, text=True, timeout=30, check=False)
                self.assertEqual(0, result.returncode, result.stderr)
                rows = [line.removeprefix('AWF_DISPATCH=') for line in result.stdout.splitlines()
                        if line.startswith('AWF_DISPATCH=')]
                self.assertEqual(1, len(rows), result.stdout)
                observed = json.loads(rows[0])
                scripts = WORKFLOW.resolve().parent
                library = ROOT / '.agentic/lib'
                # workflow and the dispatched script each add the existing lib
                # path. The sole additional import root is installed scripts.
                self.assertEqual(Counter(observed['baseline']) + Counter({str(library): 2, str(scripts): 1}),
                                 Counter(observed['paths']))
                self.assertEqual(str(scripts / (module + '.py')), str(Path(observed['origin']).resolve()))
                self.assertNotIn(str(cwd), observed['paths'])
                self.assertNotIn('', observed['paths'])
                self.assertEqual(str(cwd), observed['default_root'])
                self.assertEqual(1, observed['isolated'])
                self.assertTrue(observed['no_bytecode'])
                self.assertIn('usage:', result.stdout)


if __name__ == '__main__':
    unittest.main()
