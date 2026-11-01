from django.contrib import admin
from django.urls import path, include
# from apps.users import urls 

urlpatterns = [
    # админка 
    path('admin/', admin.site.urls),
    # аунтефикация 
    path('api/auth/', include('apps.users.urls')),
    # дашборд
    path('api/consumption/', include('apps.consumption.urls')),
    # источники и агрегации 
    path('api/aggregation/', include('apps.aggregation.urls')),
]
