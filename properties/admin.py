from django.contrib import admin

from .models import Property, PropertyImage


class PropertyImageInline(admin.TabularInline):
    model = PropertyImage
    extra = 1


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "property_type",
        "region",
        "price",
        "status",
        "is_featured",
        "created_at",
    )
    list_filter = ("property_type", "region", "status", "is_featured")
    search_fields = ("title", "address", "description")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [PropertyImageInline]
