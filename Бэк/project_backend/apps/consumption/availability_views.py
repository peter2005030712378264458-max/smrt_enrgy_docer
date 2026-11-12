"""Authenticated read-only report; never exposes adapter management operations."""
from urllib.parse import urlencode

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.aggregation.client import request_adapter


class AvailabilityParameters(serializers.Serializer):
    period = serializers.ChoiceField(choices=('day', 'week', 'month'), default='day')
    refresh = serializers.ChoiceField(choices=('0', '1'), default='0')


class DeviceAvailabilityView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        if set(request.query_params) - {'period', 'refresh'}:
            raise serializers.ValidationError('Неизвестные параметры отчёта')
        parameters = AvailabilityParameters(data=request.query_params)
        parameters.is_valid(raise_exception=True)
        result, status = request_adapter('GET', 'device-availability?' + urlencode(parameters.validated_data))
        return Response(result, status=status)
