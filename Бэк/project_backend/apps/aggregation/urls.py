from django.urls import path
from .views import AdapterView, CapabilitiesView

urlpatterns = [
    path('capabilities/', CapabilitiesView.as_view()),
    path('<path:operation>/', AdapterView.as_view()),
]
