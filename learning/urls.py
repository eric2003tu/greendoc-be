from django.urls import path

from . import views

urlpatterns = [
    path("learning/profile/", views.profile),
    path("learning/global/", views.global_model),
    path("learning/stats/", views.engine_stats),
    path("learning/terms/", views.terms),
    path("learning/terms/<str:term_id>/", views.term),
]
