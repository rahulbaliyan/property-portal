import time

from django.core.management.base import BaseCommand

from duediligence import services
from duediligence.models import TitleCheckReport


class Command(BaseCommand):
    help = (
        "Continuously polls for TitleCheckReports queued for a Bhulekh "
        "check (clicking 'Run Bhulekh Check' in the live admin just queues "
        "one) and runs the real check for each. bhulekh.uk.gov.in blocks "
        "common cloud-provider IP ranges outright — confirmed against both "
        "Render and GitHub Actions — so this has to run somewhere else "
        "entirely: a residential/office connection, not a datacenter one. "
        "Point it at the production database and leave it running:\n\n"
        "    DATABASE_URL=<production database url> python manage.py poll_bhulekh_queue\n\n"
        "See docs/BHULEKH_POLLER.md for full setup."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=10,
            help="Seconds to wait between polls when the queue is empty (default: 10).",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Process whatever is currently queued, then exit, instead of polling forever.",
        )

    def handle(self, *args, **options):
        interval = options["interval"]
        run_once = options["once"]
        self.stdout.write(
            self.style.SUCCESS(
                "Polling for queued Bhulekh checks"
                + (", once" if run_once else f", every {interval}s")
                + "... Ctrl+C to stop."
            )
        )
        try:
            while True:
                queued = list(
                    TitleCheckReport.objects.filter(
                        status=TitleCheckReport.Status.QUEUED
                    ).order_by("updated_at")
                )
                if not queued:
                    if run_once:
                        self.stdout.write("Nothing queued.")
                        return
                    time.sleep(interval)
                    continue

                for report in queued:
                    self.stdout.write(f"Running Bhulekh check for report {report.pk}...")
                    try:
                        services.run_check(report)
                    except Exception as exc:  # noqa: BLE001 — one bad report
                        # must never take the whole poller down; run_check()
                        # itself is documented never to raise, but this is
                        # the backstop in case something upstream changes.
                        self.stderr.write(
                            self.style.ERROR(f"  report {report.pk} raised: {exc}")
                        )
                        continue
                    report.refresh_from_db()
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  report {report.pk}: status={report.status}, "
                            f"risk={report.get_risk_level_display()}"
                        )
                    )

                if run_once:
                    return
        except KeyboardInterrupt:
            self.stdout.write("\nStopped.")
