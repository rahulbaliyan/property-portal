# Running the Bhulekh check poller

## Why this exists

`bhulekh.uk.gov.in` (the Uttarakhand government land-records portal the
`duediligence` app cross-checks against) blocks outbound connections from
common cloud-provider IP ranges. This was confirmed directly, not assumed:

- From Render (AWS): `ConnectTimeout` on every attempt.
- From GitHub Actions (Azure): same `ConnectTimeout`, same 15s hang.
- From a home/office internet connection: works immediately, every time.

There's no code fix for this — it's the government site's own network
policy, not a bug on our end. So the actual Bhulekh lookup has to run
from *somewhere that isn't a datacenter*, and this poller is that
somewhere: a small script that runs continuously on your own connection,
picks up checks the live admin has queued, and writes the real result
straight back to the production database.

Nothing else about the app changes because of this. "Extract with AI" and
every other admin action still run on Render as normal — this only
affects the one action that hits `bhulekh.uk.gov.in` directly.

## What actually happens when you click "Run Bhulekh Check"

1. The live admin (on Render) marks that report `Bhulekh Check Queued`
   and returns immediately — it does **not** try to reach Bhulekh itself.
2. The poller below, running on your machine, notices the queued report
   within a few seconds, runs the real check, and saves the result
   (risk level, matched khata/khasra data, mutation history) directly to
   the same database Render reads from.
3. The admin page you're looking at polls quietly in the background and
   reloads itself automatically once the result is in — usually well
   under a minute after the poller picks it up.

If the poller isn't running, a report will just sit at "queued" forever
— the page will tell you this after about 3 minutes and point back here.

## Running it

You need the production `DATABASE_URL` (the same one you've used before)
and this repo checked out locally. From the project root:

```bash
DJANGO_SETTINGS_MODULE=config.settings.production \
DATABASE_URL="<production database url>" \
python manage.py poll_bhulekh_queue
```

Leave that running. It checks for queued reports every 10 seconds
(`--interval` to change that) and processes any it finds, one at a time,
logging each result to the terminal. Press Ctrl+C to stop it.

To process whatever's currently queued once and exit, instead of running
forever:

```bash
DJANGO_SETTINGS_MODULE=config.settings.production \
DATABASE_URL="<production database url>" \
python manage.py poll_bhulekh_queue --once
```

## Keeping it running unattended

A plain terminal window works, but closes when you log out or restart.
For something that survives that, on macOS, a `launchd` agent is the
standard way to keep a script running in the background:

1. Save the two env vars somewhere private (not in this repo), e.g.
   `~/.bhulekh-poller.env`:
   ```
   DJANGO_SETTINGS_MODULE=config.settings.production
   DATABASE_URL=<production database url>
   ```
2. Create `~/Library/LaunchAgents/com.shivashakti.bhulekhpoller.plist`
   pointing at a small wrapper shell script that sources that file and
   runs `python manage.py poll_bhulekh_queue` from this repo's directory,
   then `launchctl load` it.

Any always-on machine on your home/office network works — a spare
laptop, a Mac mini, a Raspberry Pi. It doesn't need much: the script is
idle almost all the time and only does real work for the few seconds a
check takes.

## Security notes

- Whatever machine runs this has the production database credential on
  it. Treat that credential the same way you would the Render dashboard
  itself — don't leave the env file somewhere shared, and rotate the
  Neon database password if the machine is ever lost, stolen, or shared
  with someone else.
- This command only ever touches `TitleCheckReport`/`KhataLookup`/
  `MutationEntry`/`KhasraEntry` rows (via `services.run_check`) — it
  doesn't read or write anything else in the database.
