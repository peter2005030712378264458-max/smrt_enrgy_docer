import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args):
        return None


# Only the isolated test front end uses a self-signed certificate.
opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl._create_unverified_context()))


def get(path):
    request = Request('https://frontend' + path, headers={'Host': 'localhost'})
    try:
        return opener.open(request, timeout=3)
    except HTTPError as response:
        return response


for attempt in range(30):
    try:
        response = get('/admin/')
        if response.status != 502:
            break
    except URLError:
        pass
    time.sleep(.2)
else:
    raise AssertionError('Test frontend/backend did not become ready')

assert response.status == 302, response.status
assert response.headers['Location'] == '/admin/login/?next=/admin/', response.headers['Location']
response.close()
with get('/admin/login/?next=/admin/') as response:
    assert response.status == 200
    assert b'name="username"' in response.read()
for path in ('/static/admin/css/base.css', '/static/admin/js/core.js'):
    with get(path) as response:
        assert response.status == 200
        assert 'text/html' not in response.headers['Content-Type']
with get('/dashboard') as response:
    assert response.status == 200
    assert b'<div id="root">' in response.read()
with get('/api/aggregation/capabilities/') as response:
    assert response.status == 401
with get('/admin') as response:
    assert response.status == 301
    assert response.headers['Location'] == '/admin/'
print('Admin proxy smoke passed: Django login redirect/form, CSS, JS, SPA fallback and API authentication. DEBUG=0, no database.')
