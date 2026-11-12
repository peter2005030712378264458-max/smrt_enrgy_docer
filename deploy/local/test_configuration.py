import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('local_configuration', Path(__file__).with_name('configure.py'))
configuration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configuration)


class ControlIsolationTests(unittest.TestCase):
    def test_new_installations_have_different_persistent_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory)/'first.env'
            second = Path(directory)/'second.env'
            value = configuration.ensure_control_schema(first)
            self.assertRegex(value, r'^adapter_ws_v3_[0-9a-f]{12}$')
            self.assertEqual(configuration.ensure_control_schema(first), value)
            self.assertNotEqual(configuration.ensure_control_schema(second), value)

    def test_preserves_private_values_and_replaces_only_legacy_queue_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            secret_line = "OTHER_PRIVATE_SETTING='test-only-do-not-change'\n"
            trailing_line = "ANOTHER_PRIVATE_SETTING='also-preserve'\n"
            path.write_text(secret_line+'LOCAL_ADAPTER_CONTROL_SCHEMA=adapter_workstation\n\n'+trailing_line,encoding='utf-8')
            value = configuration.ensure_control_schema(path)
            text = path.read_text(encoding='utf-8')
            self.assertIn(secret_line, text)
            self.assertIn('\n\n'+trailing_line, text)
            self.assertEqual(text.count('LOCAL_ADAPTER_CONTROL_SCHEMA='), 1)
            self.assertIn(value, text)

    def test_refuses_server_or_unknown_control_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'.env'
            for value in ('adapter_control', '', 'adapter_ws_v3_test\nLOCAL_ADAPTER_CONTROL_SCHEMA=adapter_control'):
                with self.subTest(value=value):
                    path.write_text('LOCAL_ADAPTER_CONTROL_SCHEMA='+value+'\n',encoding='utf-8')
                    before = path.read_bytes()
                    with self.assertRaises(ValueError):
                        configuration.ensure_control_schema(path)
                    self.assertEqual(path.read_bytes(), before)
