from django.urls import path

from . import views

urlpatterns = [
    path("desktop/latest.json", views.latest_manifest),
    path("desktop/latest/", views.latest_info),
    path("desktop/download/<str:version>/", views.download),
    path("desktop/update/<str:version>/", views.update_download),
    path("desktop/build/", views.build),
    path("desktop/agent/next/", views.agent_next),
    path("desktop/agent/<str:request_id>/", views.agent_report),
    path("desktop/releases/", views.releases),
    path("desktop/releases/<str:version>/", views.release),
]
