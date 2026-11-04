from django.urls import path
from .views import PreferenceView, ReportsView, CurvePreviewView

urlpatterns = [
    path('preview/', CurvePreviewView.as_view()),
    path('settings/', PreferenceView.as_view()),
    path('reports/', ReportsView.as_view()),
]
