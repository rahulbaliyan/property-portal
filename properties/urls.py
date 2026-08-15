from django.urls import path

from . import views

app_name = "properties"

urlpatterns = [
    path("", views.property_list, name="list"),
    # Must come before <slug:slug>/ below, otherwise that pattern
    # greedily matches "sell/" as a property slug and this is never reached.
    path("sell/", views.sell_property, name="sell"),
    path("<slug:slug>/", views.property_detail, name="detail"),
]
