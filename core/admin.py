from django.conf import settings
from django.contrib import admin

admin.site.site_header = f"{settings.SITE_NAME} Admin"
admin.site.site_title = f"{settings.SITE_NAME} Admin"
admin.site.index_title = "Site administration"
