"""Runs on a non-cloud connection (see docs/BHULEKH_RELAY.md) and does
nothing but relay: log into the live admin, ask what's queued, fetch each
one from bhulekh.uk.gov.in directly (the one thing this server can't do
itself), and post the raw result back. No database credential ever
leaves Render — this only ever needs an admin username and password,
exactly like logging into the site in a browser.
"""

import getpass
import time

import requests
from django.core.management.base import BaseCommand, CommandError

from duediligence import bhulekh

DEFAULT_BASE_URL = "https://shivashaktidevelopers.com"


class Command(BaseCommand):
    help = (
        "Relay for the Bhulekh check queue: logs into the live admin with "
        "a username/password (no database access needed at all), polls "
        "for reports queued by clicking 'Run Bhulekh Check', fetches each "
        "one from bhulekh.uk.gov.in directly, and posts the raw result "
        "back for the server to parse and save. Run this from a "
        "residential/office connection, not a datacenter one — "
        "bhulekh.uk.gov.in blocks common cloud-provider IP ranges "
        "outright (confirmed against both Render and GitHub Actions). "
        "See docs/BHULEKH_RELAY.md for full setup."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--base-url",
            default=DEFAULT_BASE_URL,
            help=f"Live site base URL (default: {DEFAULT_BASE_URL}).",
        )
        parser.add_argument("--username", help="Admin username. Prompted for if not given.")
        parser.add_argument(
            "--password",
            help="Admin password. Prompted for (hidden input) if not given — "
            "prefer that over passing it on the command line, which other "
            "processes on the same machine can see.",
        )
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
        base_url = options["base_url"].rstrip("/")
        username = options["username"] or input("Admin username: ")
        password = options["password"] or getpass.getpass("Admin password: ")
        interval = options["interval"]
        run_once = options["once"]

        session = requests.Session()
        self._login(session, base_url, username, password)
        self.stdout.write(self.style.SUCCESS(f"Logged in to {base_url} as {username}."))
        self.stdout.write(
            self.style.SUCCESS(
                "Polling for queued Bhulekh checks"
                + (", once" if run_once else f", every {interval}s")
                + "... Ctrl+C to stop."
            )
        )

        try:
            while True:
                queued = self._fetch_queue(session, base_url)
                if not queued:
                    if run_once:
                        self.stdout.write("Nothing queued.")
                        return
                    time.sleep(interval)
                    continue

                for item in queued:
                    self._process_one(session, base_url, item)

                if run_once:
                    return
        except KeyboardInterrupt:
            self.stdout.write("\nStopped.")

    def _login(self, session: requests.Session, base_url: str, username: str, password: str) -> None:
        login_url = f"{base_url}/admin/login/"
        get_resp = session.get(login_url, timeout=15)
        get_resp.raise_for_status()
        csrf_token = session.cookies.get("csrftoken")
        if not csrf_token:
            raise CommandError(
                f"Couldn't find a CSRF cookie on {login_url} — is this really the admin login page?"
            )
        post_resp = session.post(
            login_url,
            data={
                "username": username,
                "password": password,
                "csrfmiddlewaretoken": csrf_token,
                "next": "/admin/",
            },
            headers={"Referer": login_url},
            timeout=15,
        )
        post_resp.raise_for_status()
        if "/login/" in post_resp.url:
            raise CommandError(
                "Login failed — check the username and password (the admin "
                "login form itself rejected them)."
            )

    def _fetch_queue(self, session: requests.Session, base_url: str) -> list[dict]:
        url = f"{base_url}/admin/duediligence/titlecheckreport/queue/"
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()["reports"]

    def _process_one(self, session: requests.Session, base_url: str, item: dict) -> None:
        report_id = item["id"]
        self.stdout.write(f"Fetching Bhulekh data for report {report_id}...")
        fetched = bhulekh.fetch_check_data(item["khasra_numbers"], item["location"])

        submit_url = f"{base_url}/admin/duediligence/titlecheckreport/{report_id}/run-check/submit/"
        csrf_token = session.cookies.get("csrftoken")
        resp = session.post(
            submit_url,
            json=fetched,
            headers={"X-CSRFToken": csrf_token, "Referer": submit_url},
            timeout=30,
        )
        if resp.status_code != 200:
            self.stderr.write(
                self.style.ERROR(f"  report {report_id}: server rejected the result ({resp.status_code})")
            )
            return
        result = resp.json()
        self.stdout.write(
            self.style.SUCCESS(f"  report {report_id}: status={result['status']}, risk={result['risk_level']}")
        )
