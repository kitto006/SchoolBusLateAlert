# SchoolBusLateAlert

Monitors the York Region Student Transportation alerts page
(https://bp.schoolbuscity.com/Alerts) for specific bus routes and emails
subscribers when a new alert appears for one of them.

## How it works

- `bus_alert_checker.py` loads Chromium via Playwright, reads the
  "Transportation Alerts" table, and matches rows whose `Run` column starts
  with a configured route number (e.g. `3039-2-VENT` matches route `3039`).
- Each alert is hashed and recorded in `state.json` so a recipient is only
  emailed once per distinct alert, not on every run while it's still active.
- Email is sent via the local `msmtp` command.
- Before checking anything, the script consults `school_calendar.json` and
  skips the run entirely on weekends, school holidays, and elementary PA
  days, so no alerts are sent on days there's no bus service anyway.

## Setup

1. Install dependencies:
   ```
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   playwright install chromium
   ```
2. Make sure `msmtp` is installed and configured (`~/.msmtprc`) with a
   working account.
3. Copy `config.example.json` to `config.json` and fill in:
   - `mail.msmtp_account` / `mail.from_address` — your msmtp account.
   - `routes` — route number → list of recipient email addresses.

   `config.json` is gitignored since it contains personal email addresses.
4. Update `school_calendar.json` each school year with the current term
   dates, holidays, and PA days (source: your board's published calendar).

## Usage

```
python bus_alert_checker.py            # normal run, sends email for new alerts
python bus_alert_checker.py --dry-run  # log what would be sent, no email
python bus_alert_checker.py --force    # skip the school-day calendar check
python bus_alert_checker.py --date 2026-10-12   # test against a specific date
```

## Cron

Run twice daily (8am and 3pm):

```
0 8,15 * * * cd /path/to/SchoolBusLateAlert && venv/bin/python bus_alert_checker.py >> cron.log 2>&1
```
