"""Configure local containers which use existing databases through SSH to VM42."""
import argparse
import base64
from getpass import getpass
import json
from pathlib import Path
import secrets
import re
import shutil
import subprocess
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def ensure_control_schema(path):
    """Persist a per-install v3 queue without rewriting private connection settings."""
    existing = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    pattern = re.compile(r'^LOCAL_ADAPTER_CONTROL_SCHEMA[ \t]*=[ \t]*([^\r\n]*?)[ \t]*\r?$', re.MULTILINE)
    matches = list(pattern.finditer(existing))
    if len(matches) > 1:
        raise ValueError('Duplicate LOCAL_ADAPTER_CONTROL_SCHEMA settings')
    match = matches[0] if matches else None
    if match:
        value = match.group(1).strip("'\"")
        if re.fullmatch(r'adapter_ws_v3_[a-z0-9_]{1,40}', value):
            return value
        if value != 'adapter_workstation':
            raise ValueError('LOCAL_ADAPTER_CONTROL_SCHEMA must use an isolated adapter_ws_v3_ namespace')
    value = 'adapter_ws_v3_' + secrets.token_hex(6)
    line = 'LOCAL_ADAPTER_CONTROL_SCHEMA=' + value
    if match:
        updated = existing[:match.start()] + line + existing[match.end():]
    else:
        updated = existing + ('\n' if existing and not existing.endswith('\n') else '') + line + '\n'
    path.write_text(updated, encoding='utf-8')
    return value


def write_env(path, values):
    def escape(value):
        value = str(value)
        if any(char in value for char in ('\n', '\r', '\0')):
            raise ValueError('Environment values must be single-line')
        return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"
    path.write_text(''.join(f'{key}={escape(value)}\n' for key, value in values.items()), encoding='utf-8')
    path.chmod(0o600)


def vm_settings():
    # Only the connection parameters needed by this local deployment are read.
    code = """import json,os
keys=['POSTGRES_USER','POSTGRES_PASSWORD','DJANGO_POSTGRES_DB','SOURCE_POSTGRES_USER',
'SOURCE_POSTGRES_PASSWORD','SOURCE_POSTGRES_DB','ENERGY_USE_STORED_ENERGY']
print(json.dumps({k:os.environ.get(k,'') for k in keys}))
"""
    cmd = ['docker','run','--rm','-i','--entrypoint','sshpass',
        '-v',f'{ROOT / "deploy/vm42/secrets/target_ssh_password"}:/run/secrets/password:ro',
        '-v',f'{ROOT / "deploy/vm42/secrets/known_hosts"}:/run/secrets/known_hosts:ro',
        'smart-energy-ssh-tunnel','-f','/run/secrets/password','ssh','-T',
        '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile=/run/secrets/known_hosts',
        '-o','ConnectTimeout=10','collective@193.232.208.42',
        'docker exec -i smart-energy-vm42_backend_1 python -']
    result = subprocess.run(cmd,input=code,capture_output=True,text=True,encoding='utf-8',timeout=40)
    if result.returncode:
        raise RuntimeError('Не удалось получить параметры подключения через SSH; используйте ввод вручную')
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-vm', action='store_true', help='Reuse current database connection parameters over verified SSH')
    parser.add_argument('--isolate-control', action='store_true', help='Prepare only the isolated local queue identity; no credentials or DB access')
    args = parser.parse_args()
    if args.isolate_control:
        print('Local control schema: ' + ensure_control_schema(ROOT/'.env'))
        return
    if (HERE / 'app.env').exists():
        raise SystemExit('deploy/local/app.env already exists; existing settings were preserved')
    template = dict(line.split('=',1) for line in (HERE/'app.env.example').read_text().splitlines() if '=' in line)
    if args.from_vm:
        remote = vm_settings()
        template.update({k:v for k,v in remote.items() if v})
    else:
        template['POSTGRES_USER'] = input('PostgreSQL user on .42 [postgres]: ').strip() or 'postgres'
        template['POSTGRES_PASSWORD'] = getpass('PostgreSQL password on .42: ')
        template['SOURCE_POSTGRES_USER'] = input('Source reader [student]: ').strip() or 'student'
        template['SOURCE_POSTGRES_PASSWORD'] = getpass('Source reader password: ')
    if not template['POSTGRES_PASSWORD'] or template['POSTGRES_PASSWORD']=='replace-me':
        raise SystemExit('A database password is required')
    def uri(port, database, user, password):
        return f'postgresql://{quote(user,safe="")}:{quote(password,safe="")}@ssh:{port}/{quote(database,safe="")}?connect_timeout=8'
    template['ANALYTICS_POSTGRES_USER'] = template['POSTGRES_USER']
    template['ANALYTICS_POSTGRES_PASSWORD'] = template['POSTGRES_PASSWORD']
    template['ANALYTICS_INTERNAL_TOKEN'] = secrets.token_hex(32)
    template['ENERGY_ADAPTER_TOKEN'] = secrets.token_hex(32)
    template['ENERGY_AGG_TARGET_DSN'] = uri(5432,template['DJANGO_POSTGRES_DB'],template['POSTGRES_USER'],template['POSTGRES_PASSWORD'])
    # The existing metadata reader also reads source measurements. Sessions are read-only in adapter.
    source = uri(5433,template['SOURCE_POSTGRES_DB'],template['SOURCE_POSTGRES_USER'],template['SOURCE_POSTGRES_PASSWORD'])
    template['ENERGY_AGG_SOURCE_DSN'] = source
    template['ENERGY_AGG_DIRECTORY_DSN'] = source
    private = HERE/'private'
    private.mkdir(exist_ok=True)
    key = private/'connection_key'
    if not key.exists():
        key.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)))
        key.chmod(0o444)
    for suffix in ('crt','key'):
        source = ROOT/f'ssl/nginx.{suffix}'
        if source.exists():
            shutil.copyfile(source,private/f'nginx.{suffix}')
    write_env(HERE/'app.env',template)
    ensure_control_schema(ROOT/'.env')
    print('Local settings prepared. Credentials were not printed. No database was created.')


if __name__ == '__main__':
    main()
