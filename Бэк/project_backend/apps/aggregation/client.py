import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from rest_framework.exceptions import APIException


class AdapterUnavailable(APIException):
    status_code = 503
    default_detail = 'Адаптер недоступен. Проверьте запуск сервиса и служебную схему.'


def request_adapter(method, path, payload=None):
    if not settings.ENERGY_ADAPTER_URL or not settings.ENERGY_ADAPTER_TOKEN:
        raise AdapterUnavailable()
    request = Request(settings.ENERGY_ADAPTER_URL.rstrip('/') + '/' + path,
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={'Content-Type': 'application/json', 'X-Internal-Token': settings.ENERGY_ADAPTER_TOKEN},
                      method=method)
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response), response.status
    except HTTPError as exc:
        if exc.code in (400, 404, 409):
            try:
                return json.load(exc), exc.code
            except ValueError:
                pass
        raise AdapterUnavailable() from exc
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise AdapterUnavailable() from exc
