from django.urls import path

from . import views

urlpatterns = [
    path("preferences/", views.preferences),
    path("sources/", views.source_collection),
    path("sources/<str:tag>/", views.source_detail),
    path("citations/format/", views.citation_format),
    path("bibliography/", views.bibliography),
]
