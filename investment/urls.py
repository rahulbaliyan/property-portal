from django.urls import path

from . import api, views

app_name = "investment"

urlpatterns = [
    path("reports/<int:pk>/", views.analysis_run_report, name="report"),
    path("api/properties/<int:property_id>/analysis/", api.property_analysis, name="api_property_analysis"),
    path("api/properties/<int:property_id>/score/", api.property_score, name="api_property_score"),
    path("api/properties/<int:property_id>/risks/", api.property_risks, name="api_property_risks"),
    path(
        "api/properties/<int:property_id>/comparables/",
        api.property_comparables,
        name="api_property_comparables",
    ),
    path("api/properties/<int:property_id>/analyze/", api.property_analyze, name="api_property_analyze"),
]
