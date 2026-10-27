from django.urls import path

from . import views

urlpatterns = [
    path("catalog/templates/", views.template_list),
    path("catalog/templates/<str:template_id>/", views.template_detail),
    path("catalog/design/", views.design),
    path("catalog/page-setup/", views.page_setup),
    path("catalog/reference/", views.reference),
]
