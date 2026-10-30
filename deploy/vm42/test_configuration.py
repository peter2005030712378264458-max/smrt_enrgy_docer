from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlsplit, unquote

from configure import connection_uri, render_env, write_configuration


class ConfigurationTests(unittest.TestCase):
    def test_source_and_target_uris_keep_special_passwords_and_distinct_ports(self):
        password = "p@ss:/?#% '$"
        source = urlsplit(connection_uri(25432, 'paradigm_db', 'fdw_reader', password))
        target = urlsplit(connection_uri(25433, 'student', 'postgres', password))
        self.assertEqual(unquote(source.password), password)
        self.assertEqual(unquote(target.password), password)
        self.assertEqual((source.port, source.path), (25432, '/paradigm_db'))
        self.assertEqual((target.port, target.path), (25433, '/student'))

    def test_configuration_never_overwrites_existing_credentials(self):
        settings = {key: 'test-only' for key in (
            'database_password', 'metadata_user', 'metadata_password', 'raw_password',
            'source_app_user', 'source_app_password', 'target_ssh_user',
            'source_ssh_password', 'target_ssh_password')}
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            for name in ('app.env.example', '.env.example'):
                shutil.copyfile(Path(__file__).parent / name, directory / name)
            write_configuration(directory, settings, 'test-host-key')
            original = (directory / 'app.env').read_bytes()
            with self.assertRaises(FileExistsError):
                write_configuration(directory, {**settings, 'database_password': 'changed'}, 'other-key')
            self.assertEqual((directory / 'app.env').read_bytes(), original)

    def test_environment_injection_is_rejected(self):
        with self.assertRaises(ValueError):
            render_env('PASSWORD=placeholder', {'PASSWORD': 'one\nEXTRA=two'})


if __name__ == '__main__':
    unittest.main()
