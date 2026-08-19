from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .models import Location, Property


class PropertySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Property.objects.filter(
            moderation_status=Property.ModerationStatus.APPROVED
        ).exclude(status=Property.Status.SOLD)

    def lastmod(self, obj):
        return obj.updated_at


class LocationSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return Location.objects.all()

    def location(self, obj):
        return reverse("location_detail", kwargs={"region": obj.region})
