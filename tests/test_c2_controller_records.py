"""Accepted controller main tested with a fake gateway; no imports or bind."""
import ast
import contextlib
import datetime
import json
from pathlib import Path
import tempfile
import threading
import unittest


class ControllerRecordTests(unittest.TestCase):
    def run_case(self, mode):
        source = Path(__file__).parents[1] / 'scripts/c2/templates/c2-disabled-controller.py.in'
        tree = ast.parse(source.read_text(encoding='utf-8'))
        main = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'main']
        self.assertEqual(len(main), 1)
        # Only the definition is executed; imports/path guard/service are not.
        for node in ast.walk(main[0]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, ('unlink', 'remove', 'write_text'))
        code = compile(ast.Module(body=main, type_ignores=[]), str(source), 'exec')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = root / 'disabled-controller.json'
            (root / 'stop-disabled-controller.flag').write_text('synthetic stop')
            unknown = b'synthetic concurrent public record'
            if mode == 'preexisting':
                record.write_bytes(unknown)
            calls = []

            class FakeOS:
                @staticmethod
                def getpid(): return 54321

            class FakeGateway:
                def __init__(self, host, listen_approved):
                    if host is not None or listen_approved is not True:
                        raise AssertionError('disabled constructor required')
                    calls.append('fake')

                @contextlib.contextmanager
                def serve(self, host, port):
                    if (host, port) != ('127.0.0.1', 19180):
                        raise AssertionError('unexpected endpoint')
                    if mode == 'before_create':
                        record.write_bytes(unknown)
                    try:
                        yield
                    finally:
                        if mode == 'on_exit':
                            record.write_bytes(unknown)

            scope = {'root': root, 'deployment_root': root.parent,
                     'ProductionGateway': FakeGateway, 'os': FakeOS,
                     'json': json, 'datetime': datetime, 'threading': threading}
            exec(code, scope)
            if mode in ('preexisting', 'before_create'):
                with self.assertRaises(FileExistsError):
                    scope['main']()
            else:
                scope['main']()
            self.assertEqual(calls, ['fake'])
            self.assertTrue(record.exists())
            if mode != 'normal':
                self.assertEqual(record.read_bytes(), unknown)
            else:
                result = json.loads(record.read_text())
                self.assertEqual(result['pid'], 54321)
                for key in ('credential_facility_opened', 'runpod_effects', 'journal_opened', 'tunnel_started'):
                    self.assertIs(result[key], False)

    def test_normal_exit_retains_record(self): self.run_case('normal')
    def test_preexisting_record_preserved(self): self.run_case('preexisting')
    def test_create_race_preserved(self): self.run_case('before_create')
    def test_shutdown_update_preserved(self): self.run_case('on_exit')
