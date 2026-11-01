from django.urls import path
from .views import PreferenceView, ReportsView

urlpatterns = [
    path('settings/', PreferenceView.as_view()),
    path('reports/', ReportsView.as_view()),
]
