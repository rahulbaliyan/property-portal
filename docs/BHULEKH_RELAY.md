# Running the Bhulekh check relay

## Why this exists

`bhulekh.uk.gov.in` (the Uttarakhand government land-records portal the
`duediligence` app cross-checks against) blocks outbound connections from
common cloud-provider IP ranges. This was confirmed directly, not assumed:

- From Render (AWS): `ConnectTimeout` on every attempt.
- From GitHub Actions (Azure): same `ConnectTimeout`, same 15s hang.
- From a home/office internet connection: works immediately, every time.

There's no code fix for this — it's the government site's own network
policy, not a bug on our end. So the actual Bhulekh lookup has to run
from *somewhere that isn't a datacenter*, and this relay is that
somewhere: a small script that logs into the live admin (just a username
and password — nothing else), fetches whatever's queued for a Bhulekh
check, does the actual government-site lookup from your own connection,
and posts the raw result back over HTTPS for the server to parse and
save. **No database credential is ever involved** — the relay only ever
talks to the live site the same way a browser would.

Nothing else about the app changes because of this. "Extract with AI" and
every other admin action still run on Render as normal — this only
affects the one action that hits `bhulekh.uk.gov.in` directly.

## What actually happens when you click "Run Bhulekh Check"

1. The live admin (on Render) marks that report `Bhulekh Check Queued`
   and returns immediately — it does **not** try to reach Bhulekh itself.
2. The relay below, running on your machine, notices the queued report
   within a few seconds (it polls `.../titlecheckreport/queue/`, an
   admin-only endpoint listing exactly the khasra numbers and
   district/tehsil/village codes it needs — nothing more), fetches the
   real data from `bhulekh.uk.gov.in` itself, and posts the raw result to
   `.../titlecheckreport/<id>/run-check/submit/`. The server does all the
   actual parsing, name/area matching, and risk scoring from that —
   exactly what it would do if it could reach Bhulekh directly.
3. The admin page you're looking at polls quietly in the background and
   reloads itself automatically once the result is in — usually well
   under a minute after the relay picks it up.

If the relay isn't running, a report will just sit at "queued" forever —
the page will tell you this after about 3 minutes and point back here.

## Running it

You need this repo checked out locally (just for the `bhulekh.py` module
and the management command — no database, no `.env` needed at all) and
an admin username/password for the live site:

```bash
python manage.py relay_bhulekh_check
```

It'll prompt for the username and password (password input is hidden).
To skip the prompts:

```bash
python manage.py relay_bhulekh_check --username admin
```

You can also pass `--password` on the command line, but prefer the
prompt — anything else on the same machine can read the command-line
arguments of a running process.

Leave it running. It checks the queue every 10 seconds (`--interval` to
change that) and processes whatever it finds, one at a time, logging
each result to the terminal. Press Ctrl+C to stop it.

To process whatever's currently queued once and exit, instead of running
forever:

```bash
python manage.py relay_bhulekh_check --once
```

Pointing at a non-default deployment:

```bash
python manage.py relay_bhulekh_check --base-url https://staging.example.com
```

## Keeping it running unattended

A plain terminal window works, but closes when you log out or restart.
For something that survives that, on macOS, a `launchd` agent is the
standard way to keep a script running in the background — point it at a
small wrapper shell script that runs `python manage.py
relay_bhulekh_check --username admin --password "$(cat ~/.bhulekh-relay-password)"`
from this repo's directory (keeping the password file itself private,
`chmod 600`), then `launchctl load` it.

Any always-on machine on your home/office network works — a spare
laptop, a Mac mini, a Raspberry Pi. It doesn't need much: the script is
idle almost all the time and only does real work for the few seconds a
check takes, and it never needs a database connection, Cloudinary
credentials, or any other production secret — just the admin login.

## Security notes

- Whatever machine runs this has an admin username/password on it,
  scoped to whatever that account can already do in the live admin —
  same exposure as leaving yourself logged into the admin in a browser
  there. If the machine is ever lost, stolen, or shared, change that
  account's password.
- The relay never touches the database directly and never sees anything
  beyond what `queue/` and `submit/` return — it can't read or write
  anything else in the app.
