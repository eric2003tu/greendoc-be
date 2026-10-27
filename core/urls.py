from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health),
    path("openapi.json", views.openapi_schema),
    path("docs/", views.swagger_ui),
]
