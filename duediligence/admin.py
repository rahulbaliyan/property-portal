import json

from django.contrib import admin
from django.http import (
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.utils.html import format_html

from . import services, views
from .forms import TitleCheckReportForm
from .models import KhasraEntry, KhataLookup, MutationEntry, TitleCheckReport


class KhasraEntryInline(admin.TabularInline):
    model = KhasraEntry
    fields = (
        "khasra_number",
        "order",
        "lookup_status",
        "matched_khata_number",
        "area_match",
        "seller_match",
        "buyer_match",
    )
    readonly_fields = (
        "lookup_status",
        "matched_khata_number",
        "area_match",
        "seller_match",
        "buyer_match",
    )
    extra = 2


class MutationEntryInline(admin.TabularInline):
    model = MutationEntry
    extra = 0
    can_delete = False
    fields = (
        "sequence",
        "case_number",
        "order_date",
        "seller_name",
        "buyer_name",
        "area_text",
        "is_litigation_flagged",
        "matched_litigation_keywords",
    )
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(KhataLookup)
class KhataLookupAdmin(admin.ModelAdmin):
    """Append-only audit log — browsable, not hand-edited."""

    list_display = ("report", "khata_number", "fetch_status", "fetched_at")
    list_filter = ("fetch_status",)
    inlines = [MutationEntryInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TitleCheckReport)
class TitleCheckReportAdmin(admin.ModelAdmin):
    form = TitleCheckReportForm
    inlines = [KhasraEntryInline]
    list_display = (
        "id",
        "risk_badge",
        "status",
        "property",
        "village_name",
        "created_at",
    )
    list_filter = ("status", "risk_level")
    autocomplete_fields = ("property",)
    search_fields = ("seller_name", "buyer_name", "village_name", "district_name")
    readonly_fields = (
        "status",
        "risk_level",
        "last_run_at",
        "last_run_error",
        "extraction_status",
        "extraction_error",
        "extraction_notes",
        "extraction_raw_response",
        "extracted_at",
    )
    change_form_template = "admin/duediligence/titlecheckreport/change_form.html"

    @admin.display(description="Risk")
    def risk_badge(self, obj):
        colors = {
            TitleCheckReport.RiskLevel.GREEN: "#1f8a4c",
            TitleCheckReport.RiskLevel.YELLOW: "#c9962e",
            TitleCheckReport.RiskLevel.ORANGE: "#d9730d",
            TitleCheckReport.RiskLevel.RED: "#b3261e",
            TitleCheckReport.RiskLevel.GRAY: "#555",
        }
        return format_html(
            '<span style="color:{}; font-weight:600;">{}</span>',
            colors.get(obj.risk_level, "#555"),
            obj.get_risk_level_display(),
        )

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "ajax/districts/",
                self.admin_site.admin_view(views.ajax_districts),
                name="duediligence_titlecheckreport_ajax_districts",
            ),
            path(
                "ajax/tehsils/",
                self.admin_site.admin_view(views.ajax_tehsils),
                name="duediligence_titlecheckreport_ajax_tehsils",
            ),
            path(
                "ajax/villages/",
                self.admin_site.admin_view(views.ajax_villages),
                name="duediligence_titlecheckreport_ajax_villages",
            ),
            path(
                "<int:object_id>/run-check/",
                self.admin_site.admin_view(self.run_check_view),
                name="duediligence_titlecheckreport_run_check",
            ),
            path(
                "<int:object_id>/extract-ai/",
                self.admin_site.admin_view(self.extract_deed_view),
                name="duediligence_titlecheckreport_extract_ai",
            ),
            path(
                "<int:object_id>/report/",
                self.admin_site.admin_view(self.report_view),
                name="duediligence_titlecheckreport_report",
            ),
            path(
                "<int:object_id>/status/",
                self.admin_site.admin_view(self.report_status_view),
                name="duediligence_titlecheckreport_status",
            ),
            path(
                "queue/",
                self.admin_site.admin_view(self.queued_reports_view),
                name="duediligence_titlecheckreport_queue",
            ),
            path(
                "<int:object_id>/run-check/submit/",
                self.admin_site.admin_view(self.submit_check_result_view),
                name="duediligence_titlecheckreport_submit_check_result",
            ),
        ]
        # Custom patterns first — otherwise Django's own <path:object_id>/
        # catch-all could swallow these before they're reached.
        return custom + urls

    def run_check_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        report = get_object_or_404(TitleCheckReport, pk=object_id)
        # admin_view() only enforces is_staff — per-model permission is
        # this ModelAdmin's own responsibility.
        if not self.has_change_permission(request, report):
            return HttpResponseForbidden()

        # bhulekh.uk.gov.in blocks this server's own outbound network
        # (confirmed: both Render and GitHub Actions time out reaching it,
        # while a residential connection works fine) — so the actual check
        # can't run here. Queue it instead; `relay_bhulekh_check` running
        # on a non-cloud connection (authenticated with an admin login,
        # not a database credential — see docs/BHULEKH_RELAY.md) picks
        # this up, fetches from Bhulekh itself, and posts the raw result
        # back to submit_check_result_view below.
        report.status = TitleCheckReport.Status.QUEUED
        report.last_run_error = ""
        report.save(update_fields=["status", "last_run_error", "updated_at"])
        self.message_user(
            request,
            "Bhulekh check queued. bhulekh.uk.gov.in blocks this server's "
            "network directly, so a local relay (see docs/BHULEKH_RELAY.md) "
            "picks this up from a non-cloud connection — the result appears "
            "here within a minute or two once it does.",
        )
        return redirect(
            reverse("admin:duediligence_titlecheckreport_change", args=[report.pk])
        )

    def report_status_view(self, request, object_id):
        """Tiny JSON endpoint the change-form page polls while a check is
        queued, so it can auto-reload once the real result lands instead of
        making the admin refresh by hand."""
        report = get_object_or_404(TitleCheckReport, pk=object_id)
        if not self.has_view_permission(request, report):
            return HttpResponseForbidden()
        return JsonResponse(
            {"status": report.status, "risk_level": report.get_risk_level_display()}
        )

    def queued_reports_view(self, request):
        """What `relay_bhulekh_check` (see docs/BHULEKH_RELAY.md) polls:
        every report currently queued for a Bhulekh check, with exactly
        the fields bhulekh.fetch_check_data() needs — nothing else. This
        is deliberately list-level (not tied to one object_id), same as
        the ajax/districts etc. endpoints above, so admin_view()'s
        is_staff check is the only gate — there's no single object to
        run has_view_permission against, and nothing here is sensitive
        beyond what any staff user already sees in the report list."""
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        reports = TitleCheckReport.objects.filter(status=TitleCheckReport.Status.QUEUED)
        data = [
            {
                "id": report.pk,
                "khasra_numbers": list(
                    report.khasra_entries.order_by("order", "id").values_list(
                        "khasra_number", flat=True
                    )
                ),
                "location": services.location_dict(report),
            }
            for report in reports
        ]
        return JsonResponse({"reports": data})

    def submit_check_result_view(self, request, object_id):
        """Where `relay_bhulekh_check` posts back what it fetched from
        Bhulekh for one report — see queued_reports_view and
        docs/BHULEKH_RELAY.md. The relay never touches the database or
        parses anything itself; this view does exactly what run_check()
        would with the same fetched data, via the same shared code path."""
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        report = get_object_or_404(TitleCheckReport, pk=object_id)
        if not self.has_change_permission(request, report):
            return HttpResponseForbidden()
        try:
            fetched = json.loads(request.body)
        except ValueError:  # covers JSONDecodeError and UnicodeDecodeError
            return HttpResponseBadRequest("Request body must be valid JSON.")
        if not isinstance(fetched, dict):
            return HttpResponseBadRequest("Request body must be a JSON object.")

        services.apply_remote_check_result(report, fetched)
        report.refresh_from_db()
        return JsonResponse(
            {"status": report.status, "risk_level": report.get_risk_level_display()}
        )

    def extract_deed_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        report = get_object_or_404(TitleCheckReport, pk=object_id)
        if not self.has_change_permission(request, report):
            return HttpResponseForbidden()

        services.extract_deed(report)
        report.refresh_from_db()
        self.message_user(request, report.get_extraction_status_display())
        return redirect(
            reverse("admin:duediligence_titlecheckreport_change", args=[report.pk])
        )

    def report_view(self, request, object_id):
        report = get_object_or_404(TitleCheckReport, pk=object_id)
        if not self.has_view_permission(request, report):
            return HttpResponseForbidden()
        return views.render_report(request, report, self.admin_site)
