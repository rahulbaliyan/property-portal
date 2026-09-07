from django.core.management.base import BaseCommand, CommandError

from duediligence import services
from duediligence.models import TitleCheckReport


class Command(BaseCommand):
    help = (
        "Run the Bhulekh cross-check for one specific TitleCheckReport, "
        "right now, regardless of its queue status. This needs BOTH direct "
        "database access AND a connection bhulekh.uk.gov.in doesn't block "
        "(see docs/BHULEKH_RELAY.md) — for the normal day-to-day workflow, "
        "the live admin's 'Run Bhulekh Check' button queues a report and "
        "'relay_bhulekh_check' (admin login only, no database access "
        "needed) picks it up automatically. This command is here for "
        "ad-hoc runs when you already have both of those anyway."
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
