from django.contrib.sitemaps import Sitemap

from .models import Property


class PropertySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Property.objects.exclude(status=Property.Status.SOLD)

    def lastmod(self, obj):
        return obj.updated_at
