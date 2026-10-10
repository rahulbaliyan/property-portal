from django.urls import path

from . import views

app_name = "investment"

urlpatterns = [
    path("reports/<int:pk>/", views.analysis_run_report, name="report"),
]
