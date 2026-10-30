"""One-time private configuration. No credentials are stored in tracked files."""
from getpass import getpass
import base64
import hashlib
from pathlib import Path
import secrets
import shutil
import subprocess
from urllib.parse import quote


def connection_uri(port, database, user, password):
    return (f'postgresql://{quote(user, safe="")}:{quote(password, safe="")}@127.0.0.1:'
            f'{port}/{database}?connect_timeout=8')


def render_env(template, values):
    lines = []
    for line in template.splitlines():
        key, separator, _ = line.partition('=')
        if separator and key in values:
            value = values[key]
            if any(c in value for c in ('\n', '\r', '\0')):
                raise ValueError('Environment values must be single-line strings')
            value = value.replace('\\', '\\\\').replace("'", "\\'")
            line = f"{key}='{value}'"
        lines.append(line)
    return '\n'.join(lines) + '\n'


def write_configuration(directory, settings, known_hosts):
    directory = Path(directory)
    if any((directory / name).exists() for name in ('app.env', '.env', 'secrets')):
        raise FileExistsError('Private configuration exists; edit it explicitly instead of overwriting it')
    if not all(settings.values()):
        raise ValueError('Connection accounts and passwords must not be empty')
    values = {
        'POSTGRES_PASSWORD': settings['database_password'],
        'ANALYTICS_POSTGRES_PASSWORD': settings['database_password'],
        'SOURCE_POSTGRES_USER': settings['metadata_user'],
        'SOURCE_POSTGRES_PASSWORD': settings['metadata_password'],
        'ANALYTICS_INTERNAL_TOKEN': secrets.token_hex(32),
        'ENERGY_ADAPTER_TOKEN': secrets.token_hex(32),
        'ENERGY_AGG_SOURCE_DSN': connection_uri(25432, 'paradigm_db', 'fdw_reader', settings['raw_password']),
        'ENERGY_AGG_DIRECTORY_DSN': connection_uri(25432, 'paradigm_db', settings['metadata_user'], settings['metadata_password']),
        'ENERGY_AGG_TARGET_DSN': connection_uri(25433, 'student', 'postgres', settings['database_password']),
    }
    app = render_env((directory / 'app.env.example').read_text(encoding='utf-8'), values)
    compose = render_env((directory / '.env.example').read_text(encoding='utf-8'), {
        'SOURCE_APP_USER': settings['source_app_user'],
        'VM42_SSH_USER': settings['target_ssh_user'],
    })
    for name, text in (('app.env', app), ('.env', compose)):
        path = directory / name
        path.write_text(text, encoding='utf-8')
        path.chmod(0o600)
    private = directory / 'secrets'
    private.mkdir(mode=0o700)
    for name, value in {
        'database_password': settings['database_password'],
        'source_app_password': settings['source_app_password'],
        'source_ssh_password': settings['source_ssh_password'],
        'target_ssh_password': settings['target_ssh_password'],
        'known_hosts': known_hosts.rstrip('\n'),
    }.items():
        path = private / name
        path.write_text(value + '\n', encoding='utf-8')
        # The directory protects host access; mounted PostgreSQL secrets must
        # also be readable by the non-root container user.
        path.chmod(0o444)
    (directory / 'backups').mkdir(mode=0o700, exist_ok=True)


def main():
    directory = Path(__file__).resolve().parent
    if (directory / 'app.env').exists():
        raise SystemExit('Configuration already exists. Existing files were preserved.')
    scan = subprocess.run(['ssh-keyscan', '-T', '5', '193.232.208.50', '193.232.208.42'],
                          capture_output=True, text=True, check=True).stdout
    hosts = set()
    for line in scan.splitlines():
        parts = line.split()
        if len(parts) == 3 and not line.startswith('#'):
            hosts.add(parts[0])
            digest = base64.b64encode(hashlib.sha256(base64.b64decode(parts[2])).digest()).decode().rstrip('=')
            print(parts[0], parts[1], 'SHA256:' + digest)
    if hosts != {'193.232.208.50', '193.232.208.42'}:
        raise SystemExit('Could not obtain host keys for both VMs')
    if input('Verify these fingerprints with the administrator. Continue? [yes/no] ').strip() != 'yes':
        raise SystemExit('No private configuration was written')
    settings = {
        'target_ssh_user': input('SSH user on .42 [collective]: ').strip() or 'collective',
        'target_ssh_password': getpass('SSH password on .42: '),
        'source_ssh_password': getpass('restricted_user SSH password on .50: '),
        'database_password': getpass('Password for postgres in the new application database: '),
        'raw_password': getpass('fdw_reader PostgreSQL password on .50: '),
        'metadata_user': input('Metadata reader on .50 [student]: ').strip() or 'student',
        'metadata_password': getpass('Metadata reader PostgreSQL password: '),
        'source_app_user': input('Existing student database reader [student]: ').strip() or 'student',
        'source_app_password': getpass('Existing student database PostgreSQL password (migration only): '),
    }
    write_configuration(directory, settings, scan)
    ssl = directory / 'ssl'
    ssl.mkdir(exist_ok=True)
    if not (ssl / 'nginx.crt').exists() and not (ssl / 'nginx.key').exists():
        openssl = shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe'
        subprocess.run([openssl, 'req', '-x509', '-nodes', '-days', '365', '-newkey', 'rsa:2048',
                        '-keyout', str(ssl / 'nginx.key'), '-out', str(ssl / 'nginx.crt'),
                        '-subj', '/CN=193.232.208.42', '-addext',
                        'subjectAltName=DNS:localhost,IP:127.0.0.1,IP:193.232.208.42'], check=True)
    if not all((ssl / name).exists() for name in ('nginx.crt', 'nginx.key')):
        raise SystemExit('Provide both ssl/nginx.crt and ssl/nginx.key before starting')
    (ssl / 'nginx.key').chmod(0o600)
    print('Private configuration prepared. Credentials were not printed.')


if __name__ == '__main__':
    main()
