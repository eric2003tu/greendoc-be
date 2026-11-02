from django.conf import settings
from django.contrib import admin

from .models import EngineDay, TermStat
from .stats import engine_summary


class PublishedFilter(admin.SimpleListFilter):
    title = "status"
    parameter_name = "status"

    def lookups(self, request, model_admin):
        return [("published", "In the shared dictionary"), ("pending", "Waiting for more people")]

    def queryset(self, request, queryset):
        k = settings.LEARNING_MIN_CONTRIBUTORS
        if self.value() == "published":
            return queryset.filter(contributors__gte=k)
        if self.value() == "pending":
            return queryset.filter(contributors__lt=k)
        return queryset


@admin.register(TermStat)
class TermStatAdmin(admin.ModelAdmin):
    """The shared engine: watch it grow, and remove a term that should not be in the dictionary.

    The engine stores no user identity, so there is nothing here about who taught a term."""

    list_display = ("key", "kind", "contributors", "count", "in_dictionary", "removed", "created_at")
    list_filter = (PublishedFilter, "kind", "removed")
    search_fields = ("key",)
    ordering = ("-contributors", "-count")
    date_hierarchy = "created_at"
    readonly_fields = ("kind", "key", "count", "contributors", "created_at", "updated_at")
    fields = ("kind", "key", "count", "contributors", "removed", "created_at", "updated_at")

    @admin.display(boolean=True, description="Published")
    def in_dictionary(self, obj):
        return obj.contributors >= settings.LEARNING_MIN_CONTRIBUTORS and not obj.removed

    def has_add_permission(self, request):
        return False

    def changelist_view(self, request, extra_context=None):
        context = {"engine": engine_summary(), **(extra_context or {})}
        return super().changelist_view(request, extra_context=context)


@admin.register(EngineDay)
class EngineDayAdmin(admin.ModelAdmin):
    """Daily activity of the engine (counts only)."""

    list_display = ("day", "uploads", "new_terms", "uses_added")
    readonly_fields = ("day", "uploads", "new_terms", "uses_added")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
