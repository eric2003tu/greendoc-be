from django.urls import path

from . import views

urlpatterns = [
    path("proofing/spellcheck/", views.spellcheck),
    path("proofing/readability/", views.readability),
    path("proofing/stats/", views.stats),
    path("proofing/synonyms/", views.synonyms),
]
