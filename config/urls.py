"""
URL configuration for config project.

For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path

from core.sitemaps import StaticViewSitemap
from properties import views as properties_views
from properties.sitemaps import LocationSitemap, PropertySitemap

sitemaps = {
    "static": StaticViewSitemap,
    "properties": PropertySitemap,
    "locations": LocationSitemap,
}

urlpatterns = [
    path("admin/", admin.site.urls),
    path("sitemap.xml", sitemap, {"sitemaps": sitemaps}, name="sitemap"),
    path("properties/", include("properties.urls")),
    # Root-level, matching the brief's exact requested URL shape
    # (/properties-in-dehradun/, not nested under /properties/).
    path(
        "properties-in-<str:region>/",
        properties_views.location_detail,
        name="location_detail",
    ),
    path("", include("core.urls")),
]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
