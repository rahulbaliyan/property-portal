from django.contrib.sitemaps import Sitemap
from django.urls import reverse


class StaticViewSitemap(Sitemap):
    priority = 1.0
    changefreq = "daily"

    def items(self):
        return [
            "core:home",
            "properties:list",
            "properties:sell",
            "core:about",
            "core:services",
            "core:contact",
        ]

    def location(self, item):
        return reverse(item)
