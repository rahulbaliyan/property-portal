from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.home, name="home"),
    path("about/", views.about, name="about"),
    path("services/", views.services, name="services"),
    path("contact/", views.contact, name="contact"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path(
        "google6ad03570e70caaee.html",
        views.google_site_verification,
        name="google_site_verification",
    ),
]
