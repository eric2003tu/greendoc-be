from django.contrib import admin

from .models import Comment, Document, DocumentVersion, MergeDataset, Revision


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "id", "client_id", "updated_at", "share_token")
    search_fields = ("title", "id", "client_id")
    readonly_fields = ("created_at", "updated_at")


@admin.register(DocumentVersion)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = ("document", "label", "automatic", "created_at")


admin.site.register([Comment, Revision, MergeDataset])
