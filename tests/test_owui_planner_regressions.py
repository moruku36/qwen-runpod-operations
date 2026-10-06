"""Planner guards and the module-order NameError regression, synthetic source."""
import ast
import hashlib
import unittest
from qmc_runpod.owui_installer import build_plan
from qmc_runpod.c1_ports import PortError
from test_execution_boundary import main_source

class PlannerRegression(unittest.TestCase):
    def setUp(self):
        self.source=main_source()
        self.sha=hashlib.sha256(self.source).hexdigest()
        self.plan=build_plan(self.source,version='0.11.4',expected_sha256=self.sha)

    def test_public_signature_exact_and_context_internal_only(self):
        old=next(n for n in ast.parse(self.source).body if isinstance(n,ast.AsyncFunctionDef) and n.name=='chat_completion')
        tree=ast.parse(self.plan.patched)
        public=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='chat_completion')
        internal=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='qmc_original_chat_completion')
        self.assertEqual(ast.dump(old.args),ast.dump(public.args))
        self.assertEqual([arg.arg for arg in internal.args.kwonlyargs],['qmc_context'])

    def test_real_module_name_order_and_internal_alias_bindings(self):
        selected=[]
        for node in ast.parse(self.plan.patched).body:
            if isinstance(node,ast.AsyncFunctionDef) and node.name in ('chat_completion','qmc_original_chat_completion'):
                selected.append(ast.AsyncFunctionDef(name=node.name,args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),body=[ast.Pass()],decorator_list=[]))
            elif isinstance(node,ast.Assign) and isinstance(node.value,ast.Name) and any(isinstance(t,ast.Name) and t.id in ('generate_chat_completion','generate_chat_completions') for t in node.targets): selected.append(node)
        scope={}
        exec(compile(ast.fix_missing_locations(ast.Module(body=selected,type_ignores=[])),'name-order-only','exec'),scope)
        self.assertIs(scope['generate_chat_completion'],scope['qmc_original_chat_completion'])
        self.assertIs(scope['generate_chat_completions'],scope['qmc_original_chat_completion'])
        self.assertIsNot(scope['chat_completion'],scope['qmc_original_chat_completion'])

    def test_wrong_source_hash_refused(self):
        with self.assertRaisesRegex(PortError,'source_compare_and_swap_failed'): build_plan(self.source,version='0.11.4',expected_sha256='0'*64)

    def test_new_public_kwonly_parameter_refused(self):
        source=self.source.replace(b'    user=Depends(get_verified_user),\n',b'    user=Depends(get_verified_user),\n    *, injected=None,\n',1)
        with self.assertRaisesRegex(PortError,'manual_entry_shape_changed'): build_plan(source,version='0.11.4',expected_sha256=hashlib.sha256(source).hexdigest())

    def test_verified_user_dependency_change_refused(self):
        source=self.source.replace(b'    user=Depends(get_verified_user),\n',b'    user=Depends(get_current_user),\n',1)
        with self.assertRaisesRegex(PortError,'verified_user_dependency_required'): build_plan(source,version='0.11.4',expected_sha256=hashlib.sha256(source).hexdigest())

    def test_already_patched_source_refused(self):
        with self.assertRaisesRegex(PortError,'already_patched_or_local_conflict'): build_plan(self.plan.patched,version='0.11.4',expected_sha256=self.plan.patched_sha256)

    def test_missing_legacy_alias_refused_without_weakening_guard(self):
        source=self.source.replace(b'generate_chat_completion = chat_completion',b'# omitted fixture alias',1)
        with self.assertRaisesRegex(PortError,'legacy_alias_shape_changed'): build_plan(source,version='0.11.4',expected_sha256=hashlib.sha256(source).hexdigest())
