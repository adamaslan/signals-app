"""Network-free recipe adapter regressions. No provider or data calls."""
import ast
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('signals_app.scan_recipes', ROOT / 'src/signals_app/scan_recipes/__init__.py', submodule_search_locations=[])
recipe_module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = recipe_module
spec.loader.exec_module(recipe_module)

class RecipeAdapterBoundaryTests(unittest.TestCase):
    def test_timeout_becomes_reportable_runtime_error(self):
        with patch.object(recipe_module, 'files', return_value=Path('/synthetic-only')), patch.object(recipe_module.subprocess, 'run', side_effect=subprocess.TimeoutExpired('synthetic', 30)):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                recipe_module.execute({'action': 'list'})

    def test_conflicting_module_selectors_are_rejected_before_execution(self):
        with patch.object(sys, 'argv', ['synthetic', '--recipe', 'r1', '--method', 'm1']), patch.object(recipe_module, 'execute') as execute:
            with self.assertRaises(SystemExit) as raised:
                recipe_module.main()
            self.assertEqual(raised.exception.code, 2)
            execute.assert_not_called()

    def test_typer_recipe_function_rejects_conflicting_selectors_before_input_read(self):
        # Extract only the real function definition, not the dependency-heavy app.
        source = (ROOT / 'src/signals_app/cli/main.py').read_text()
        function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'recipes')
        function.decorator_list = []
        function.returns = None
        for arg in function.args.args:
            arg.annotation = None
        module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
        class BadParameter(ValueError): pass
        class TyperStub: pass
        TyperStub.BadParameter = BadParameter
        namespace = {'typer': TyperStub, '_print_json': lambda value: None, '_fail': lambda value: None}
        exec(compile(module, str(ROOT / 'src/signals_app/cli/main.py'), 'exec'), namespace)
        with patch.object(recipe_module, 'execute') as execute:
            with self.assertRaisesRegex(BadParameter, 'exactly one'):
                namespace['recipes'](recipe='r1', method='m1', list_methods=False, input_path=Path('/does-not-exist'), list_only=False, scope='', query='')
            execute.assert_not_called()

if __name__ == '__main__': unittest.main()
