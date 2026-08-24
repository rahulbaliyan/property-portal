from django.core.management.base import BaseCommand, CommandError

from duediligence import services
from duediligence.models import TitleCheckReport


class Command(BaseCommand):
    help = (
        "Run the Bhulekh cross-check for a TitleCheckReport. Exists because "
        "bhulekh.uk.gov.in appears to block Render's outbound IP range "
        "(ConnectTimeout from production, confirmed working from a non-cloud "
        "network) — run this locally against DATABASE_URL pointed at "
        "production instead of clicking 'Run Bhulekh Check' in the live admin."
    )

    def add_arguments(self, parser):
        parser.add_argument("report_id", type=int)

    def handle(self, *args, **options):
        try:
            report = TitleCheckReport.objects.get(pk=options["report_id"])
        except TitleCheckReport.DoesNotExist as exc:
            raise CommandError(f"No TitleCheckReport with id={options['report_id']}") from exc

        services.run_check(report)
        report.refresh_from_db()

        if report.status == TitleCheckReport.Status.FAILED:
            self.stderr.write(self.style.ERROR(f"Failed: {report.last_run_error}"))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"Report {report.pk}: status={report.status}, "
                    f"risk={report.get_risk_level_display()}"
                )
            )
