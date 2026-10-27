from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("core.urls")),
    path("api/", include("documents.urls")),
    path("api/", include("proofing.urls")),
    path("api/", include("catalog.urls")),
    path("api/", include("workspace.urls")),
    path("api/", include("accounts.urls")),
    path("api/", include("support.urls")),
]
