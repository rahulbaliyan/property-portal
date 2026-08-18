from django.contrib import admin
from django.utils.html import format_html

from .models import Location, Property, PropertyImage, PropertyVideo


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("get_region_display", "content_status")
    fields = ("region", "description", "image")

    @admin.display(description="Region")
    def get_region_display(self, obj):
        return obj.get_region_display()

    @admin.display(description="Content")
    def content_status(self, obj):
        missing = []
        if not obj.description:
            missing.append("description")
        if not obj.image:
            missing.append("image")
        if not missing:
            return format_html('<span style="color:#1f8a4c;font-weight:600;">Complete</span>')
        return format_html(
            '<span style="color:#c9962e;font-weight:600;">Missing {}</span>',
            " & ".join(missing),
        )


class PropertyImageInline(admin.TabularInline):
    model = PropertyImage
    extra = 1


class PropertyVideoInline(admin.TabularInline):
    model = PropertyVideo
    extra = 1


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "moderation_badge",
        "listing_source",
        "property_type",
        "listing_intent",
        "region",
        "price",
        "status",
        "is_featured",
        "created_at",
    )
    list_filter = (
        "moderation_status",
        "listing_source",
        "property_type",
        "listing_intent",
        "region",
        "status",
        "is_featured",
    )
    search_fields = ("title", "address", "description", "seller_name", "seller_phone")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [PropertyImageInline, PropertyVideoInline]
    actions = ["approve_listings", "reject_listings"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "title",
                    "slug",
                    "property_type",
                    "listing_intent",
                    "region",
                    "address",
                    "price",
                    "area_value",
                    "area_unit",
                    "bedrooms",
                    "bathrooms",
                    "description",
                    "latitude",
                    "longitude",
                    "status",
                    "is_featured",
                )
            },
        ),
        (
            "Moderation",
            {
                "fields": ("listing_source", "moderation_status"),
                "description": "Seller submissions land here as \"Pending Review\" "
                "and stay invisible to the public until approved.",
            },
        ),
        (
            "Seller Information (private — never shown publicly)",
            {
                "classes": ("collapse",),
                "fields": ("seller_name", "seller_phone", "seller_email"),
            },
        ),
    )

    @admin.display(description="Status")
    def moderation_badge(self, obj):
        colors = {
            Property.ModerationStatus.APPROVED: "#1f8a4c",
            Property.ModerationStatus.PENDING: "#c9962e",
            Property.ModerationStatus.REJECTED: "#b3261e",
        }
        color = colors.get(obj.moderation_status, "#555")
        return format_html(
            '<span style="color:{}; font-weight:600;">{}</span>',
            color,
            obj.get_moderation_status_display(),
        )

    @admin.action(description="Approve selected listings")
    def approve_listings(self, request, queryset):
        updated = queryset.update(moderation_status=Property.ModerationStatus.APPROVED)
        self.message_user(request, f"{updated} listing(s) approved.")

    @admin.action(description="Reject selected listings")
    def reject_listings(self, request, queryset):
        updated = queryset.update(moderation_status=Property.ModerationStatus.REJECTED)
        self.message_user(request, f"{updated} listing(s) rejected.")
