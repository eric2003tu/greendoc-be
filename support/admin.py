from django.contrib import admin

from .models import SupportMessage


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ("subject", "email", "resolved", "created_at")
    list_filter = ("resolved",)
    search_fields = ("subject", "email", "message")
