from django.urls import path

from . import views

urlpatterns = [
    path("documents/", views.document_collection),
    path("documents/<uuid:doc_id>/", views.document_detail),
    path("documents/<uuid:doc_id>/share/", views.document_share),
    path("shared/<str:token>/", views.shared_document),
]
