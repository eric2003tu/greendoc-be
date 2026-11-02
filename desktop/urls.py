from django.urls import path

from . import views

urlpatterns = [
    path("desktop/latest.json", views.latest_manifest),
    path("desktop/latest/", views.latest_info),
    path("desktop/download/<str:version>/", views.download),
    path("desktop/build/", views.build),
    path("desktop/releases/", views.releases),
    path("desktop/releases/<str:version>/", views.release),
]
