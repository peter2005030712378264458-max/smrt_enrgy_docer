"""Exercise the real Django gateway and adapter worker in the disposable UI fixture."""
import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import psycopg


def request(path, payload=None, token=None, base='http://127.0.0.1:5000/api/'):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = Request(base + path, data=json.dumps(payload).encode() if payload is not None else None, headers=headers)
    with urlopen(req, timeout=20) as response:
        return json.load(response)


def completed(job, token):
    for _ in range(100):
        result = request(f"aggregation/jobs/{job['id']}/", token=token)
        if result['status'] in ('succeeded', 'failed'):
            assert result['status'] == 'succeeded', result.get('error')
            return result
        time.sleep(.2)
    raise AssertionError('Job did not finish')


try:
    request('aggregation/jobs/')
    raise AssertionError('Unauthenticated request unexpectedly allowed')
except HTTPError as exc:
    assert exc.code == 401
try:
    request('health', base='http://127.0.0.1:8002/')
    raise AssertionError('Private API allowed a missing token')
except HTTPError as exc:
    assert exc.code == 403

token = request('auth/login/', {'email': 'operator@example.test', 'password': 'AdapterTest123!'})['access']
assert request('aggregation/capabilities/', token=token)['can_manage']
profiles = request('aggregation/profiles/', token=token)
profile = next(p for p in profiles if p['document'].get('columns'))
inspect = request('aggregation/inspect/', {'connection': 'paradigm', 'source_table': 'public.customer'}, token)
inspected = completed(inspect, token)
assert inspected['events'][0]['suggestions']['ts']['column'] == 'время'
period = {'profile_id': profile['id'], 'from': '2026-10-15', 'to': '2026-10-16'}
preview = completed(request('aggregation/jobs/', {**period, 'kind': 'preview'}, token), token)
applied = completed(request('aggregation/jobs/', {**period, 'kind': 'apply', 'preview_id': preview['id']}, token), token)
day = next(e for e in applied['events'] if e['event'] == 'day')
assert day['hourly_rows'] == 24 and day['daily_rows'] == 1 and day['written']
request(f"aggregation/profiles/{profile['id']}/activate/", {}, token)
assert request('aggregation/schedule/', token=token)['active_profile']['id'] == profile['id']
with psycopg.connect('host=db dbname=test_adapter_target user=postgres password=adapter-test-only') as conn:
    assert conn.execute('SELECT SUM(energy_kwh) FROM student_schema.electricity_sensor_readings_hourly').fetchone()[0] == 48
print('HTTP smoke passed: JWT/permissions, private token, inspection, preview, apply, activation and database result.')
