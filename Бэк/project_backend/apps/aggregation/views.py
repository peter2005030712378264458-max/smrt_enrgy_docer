import re
from urllib.parse import urlencode

from django.conf import settings
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .client import request_adapter


class CapabilitiesView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        return Response({'can_manage': bool(request.user.is_staff),
                         'stored_energy_enabled': settings.ENERGY_USE_STORED_ENERGY})


class AdapterView(APIView):
    permission_classes = (IsAdminUser,)

    def forward(self, request, operation):
        allowed_get = r'(health|connections|tables|profiles|schedule|jobs|jobs/[0-9a-f-]{36})'
        allowed_post = r'(inspect|profiles|profiles/[0-9a-f-]{36}/activate|jobs|schedule)'
        pattern = allowed_get if request.method == 'GET' else allowed_post
        if not re.fullmatch(pattern, operation):
            return Response({'detail': 'Неизвестная операция'}, status=404)
        if request.method == 'GET' and operation == 'tables':
            operation += '?' + urlencode({'connection': request.query_params.get('connection', '')})
        payload = request.data if request.method == 'POST' else None
        result, status = request_adapter(request.method, operation, payload)
        return Response(result, status=status)

    get = forward
    post = forward
