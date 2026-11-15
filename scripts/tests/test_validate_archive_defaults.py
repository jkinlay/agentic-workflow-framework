"""Keep archive smoke expectations aligned with shipped fresh-install defaults."""
import ast
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '.agentic/lib'))
from agentic import VERSION


def same_expression(node, expression):
    return ast.unparse(node) == ast.unparse(ast.parse(expression, mode='eval').body)


def archive_expectation(expression):
    tree = ast.parse((ROOT / 'scripts/validate_archive.py').read_text(encoding='utf-8'))
    assertion, = [node for node in ast.walk(tree)
                  if isinstance(node, ast.Assert) and isinstance(node.test, ast.Compare)
                  and same_expression(node.test.left, expression)]
    comparison = assertion.test
    if len(comparison.ops) != 1 or len(comparison.comparators) != 1:
        raise AssertionError('Fresh-install expectation must remain a single comparison')
    return comparison


def evaluate(expression, **values):
    return eval(compile(ast.Expression(expression), '<shipped-status-format>', 'eval'),
                {'__builtins__': {}, 'VERSION': VERSION}, values)


class ArchiveDefaultTests(unittest.TestCase):
    def test_jira_enabled_expectation_matches_shipped_template(self):
        template = json.loads((ROOT / '.agentic/templates/source-config/PROJECT_CONFIG.yaml')
                              .read_text(encoding='utf-8'))
        expectation = archive_expectation("installed_configuration['jira']['enabled']")
        self.assertIsInstance(expectation.ops[0], ast.Is)
        self.assertIs(ast.literal_eval(expectation.comparators[0]), template['jira']['enabled'])

    def test_unconfigured_line_expectation_matches_shipped_status_format(self):
        tree = ast.parse((ROOT / '.agentic/lib/agentic/providers/github_status.py')
                         .read_text(encoding='utf-8'))
        status, = [node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == 'project_status']
        rejected, = [node for node in ast.walk(status) if isinstance(node, ast.If)
                     and same_expression(node.test, "report['status'] != 'ACCEPTED'")]
        update, = [node.value for node in rejected.body if isinstance(node, ast.Expr)
                   and isinstance(node.value, ast.Call)
                   and same_expression(node.value.func, 'result.update')]
        line, = [keyword.value for keyword in update.keywords if keyword.arg == 'line']
        accepted, = [node for node in ast.walk(status) if isinstance(node, ast.If)
                     and same_expression(node.test, "operating['status'] == 'ACCEPTED'")]
        suffix, = [node.value for node in accepted.body if isinstance(node, ast.Assign)
                   and len(node.targets) == 1 and same_expression(node.targets[0], 'stream_suffix')]
        append, = [node for node in accepted.body if isinstance(node, ast.AugAssign)
                   and same_expression(node.target, "result['line']")]
        self.assertIsInstance(append.op, ast.Add)
        self.assertTrue(same_expression(append.value, 'stream_suffix'))
        config = json.loads((ROOT / '.agentic/templates/source-config/PROJECT_CONFIG.yaml')
                            .read_text(encoding='utf-8'))
        operating = json.loads((ROOT / '.agentic/templates/source-config/OPERATING_CONFIG.yaml')
                               .read_text(encoding='utf-8'))
        rendered = evaluate(line) + evaluate(suffix, operating={
            'streams': operating['streams']['count'],
            'effective_ceiling': config['execution']['max_parallel_tickets'],
        })
        expectation = archive_expectation("unconfigured_status['line']")
        self.assertIsInstance(expectation.ops[0], ast.Eq)
        self.assertEqual(evaluate(expectation.comparators[0]), rendered)


if __name__ == '__main__':
    unittest.main()
