from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "id", "client_id", "updated_at", "share_token")
    search_fields = ("title", "id", "client_id")
    readonly_fields = ("created_at", "updated_at")
