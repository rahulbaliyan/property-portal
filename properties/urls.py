from django.urls import path

from . import views

app_name = "properties"

urlpatterns = [
    path("", views.property_list, name="list"),
    path("<slug:slug>/", views.property_detail, name="detail"),
]
