#!/usr/bin/env python3
"""Check bp.schoolbuscity.com/Alerts for configured bus routes and email subscribers on new alerts."""

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from datetime import date, datetime
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config.json"
LOG_PATH = SCRIPT_DIR / "bus_alert_checker.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("bus_alert_checker")


def load_json(path: Path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data):
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    tmp_path.replace(path)


def route_prefix(run_text: str) -> str:
    return run_text.strip().split("-")[0].strip()


def extract_transportation_alerts(page, timeout_ms: int):
    # The alerts table lives inside a wrapper div that stays display:none when
    # there are currently no alerts, so visibility-based waits/innerText reads
    # return nothing in that (common) case. Target the table by its stable id
    # and read via textContent, which works regardless of CSS visibility.
    table = page.locator("table#tBusNotifications")
    if table.count() == 0:
        # Fallback in case the site changes the element id: locate by heading text.
        heading = page.locator("span:has-text('Transportation Alerts')").first
        table = heading.locator("xpath=following::table[1]")
    table.wait_for(state="attached", timeout=timeout_ms)
    table.locator("tbody tr").first.wait_for(state="attached", timeout=timeout_ms)

    table_id = table.get_attribute("id")
    if table_id:
        length_select = page.locator(f"select[aria-controls='{table_id}']")
        try:
            if length_select.count() > 0:
                options = length_select.locator("option").all_text_contents()
                numeric_options = [o for o in options if o.strip().isdigit()]
                if numeric_options:
                    largest = max(numeric_options, key=lambda o: int(o.strip()))
                    length_select.select_option(label=largest)
                    page.wait_for_timeout(500)
        except Exception:
            log.debug("Could not adjust page length selector", exc_info=True)

    headers = [h.strip() for h in table.locator("thead th").all_text_contents()]
    if not headers:
        return []

    rows = table.locator("tbody tr")
    row_count = rows.count()
    alerts = []
    for i in range(row_count):
        cells = rows.nth(i).locator("td").all_text_contents()
        cells = [c.strip() for c in cells]
        if len(cells) == 1:
            text = cells[0].lower()
            if "no data" in text or "no matching records" in text or "functioning normally" in text:
                continue
        if len(cells) != len(headers):
            continue
        record = {headers[j]: cells[j] for j in range(len(headers)) if headers[j]}
        alerts.append(record)
    return alerts


def fetch_alerts(url: str, timeout_ms: int, headless: bool):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            return extract_transportation_alerts(page, timeout_ms)
        finally:
            browser.close()


def alert_hash(record: dict) -> str:
    key_fields = ["Effective", "Run", "Operator", "Status", "Affected Schools", "Created"]
    key = "|".join(record.get(f, "") for f in key_fields)
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def send_email(mail_cfg: dict, to_addrs: list, subject: str, body: str):
    msg = EmailMessage()
    msg["From"] = mail_cfg["from_address"]
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.set_content(body)

    msmtp_path = mail_cfg.get("msmtp_path", "msmtp")
    account = mail_cfg.get("msmtp_account")
    cmd = [msmtp_path]
    if account:
        cmd += ["-a", account]
    cmd += to_addrs

    result = subprocess.run(cmd, input=msg.as_bytes(), capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"msmtp failed ({result.returncode}): {result.stderr.decode(errors='replace')}"
        )


def format_alert_body(route: str, records: list, source_url: str) -> str:
    lines = [f"New transportation alert(s) detected for route {route}:", ""]
    for r in records:
        lines.append(f"Run: {r.get('Run', '')}")
        lines.append(f"Operator: {r.get('Operator', '')}")
        lines.append(f"Status: {r.get('Status', '')}")
        lines.append(f"Affected Schools: {r.get('Affected Schools', '')}")
        lines.append(f"Effective: {r.get('Effective', '')}")
        lines.append(f"Created: {r.get('Created', '')}")
        comment = r.get("Comment", "")
        if comment:
            lines.append(f"Comment: {comment}")
        lines.append("")
    lines.append(f"Source: {source_url}")
    return "\n".join(lines)


def parse_iso_date(text: str) -> date:
    return datetime.strptime(text.strip(), "%Y-%m-%d").date()


def is_school_day(check_date: date, calendar: dict) -> bool:
    if check_date.weekday() >= 5:  # Saturday/Sunday
        return False

    if "first_day" in calendar and check_date < parse_iso_date(calendar["first_day"]):
        return False
    if "last_day" in calendar and check_date > parse_iso_date(calendar["last_day"]):
        return False

    for holiday in calendar.get("holidays", []):
        start = parse_iso_date(holiday["start"])
        end = parse_iso_date(holiday.get("end", holiday["start"]))
        if start <= check_date <= end:
            return False

    for pa_day in calendar.get("pa_days_elementary", []):
        if parse_iso_date(pa_day) == check_date:
            return False

    return True


def main():
    parser = argparse.ArgumentParser(description="Check school bus alerts and email subscribers on new alerts.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to config.json")
    parser.add_argument("--state", default=None, help="Path to state.json (defaults to config's state_file)")
    parser.add_argument("--dry-run", action="store_true", help="Do not send emails, just log what would be sent")
    parser.add_argument("--date", default=None, help="Override today's date (YYYY-MM-DD) for testing the school-day check")
    parser.add_argument("--force", action="store_true", help="Run even if today is not a school day")
    args = parser.parse_args()

    config_path = Path(args.config)
    config = load_json(config_path, None)
    if config is None:
        log.error("Config file not found: %s", config_path)
        sys.exit(1)

    today = parse_iso_date(args.date) if args.date else date.today()
    calendar_rel_path = config.get("school_calendar_file")
    if calendar_rel_path and not args.force:
        calendar_path = SCRIPT_DIR / calendar_rel_path
        calendar = load_json(calendar_path, None)
        if calendar is None:
            log.warning("School calendar file not found: %s; proceeding without the school-day check.", calendar_path)
        elif not is_school_day(today, calendar):
            log.info("%s is not a school day; skipping alert check.", today.isoformat())
            return

    state_path = Path(args.state) if args.state else SCRIPT_DIR / config.get("state_file", "state.json")
    state = load_json(state_path, {"notified": {}})
    notified = state.setdefault("notified", {})

    routes_cfg = config.get("routes", {})
    if not routes_cfg:
        log.warning("No routes configured in %s; nothing to do.", config_path)
        return

    url = config.get("alert_url", "https://bp.schoolbuscity.com/Alerts")
    timeout_ms = config.get("page_timeout_ms", 30000)
    headless = config.get("headless", True)

    try:
        records = fetch_alerts(url, timeout_ms, headless)
    except PlaywrightTimeoutError as e:
        log.error("Timed out loading alerts page: %s", e)
        sys.exit(1)

    log.info("Fetched %d transportation alert row(s) from %s", len(records), url)

    current_hashes_by_route = {route: set() for route in routes_cfg}
    matches_by_route = {route: [] for route in routes_cfg}

    for record in records:
        prefix = route_prefix(record.get("Run", ""))
        for route in routes_cfg:
            if prefix == route:
                matches_by_route[route].append(record)
                current_hashes_by_route[route].add(alert_hash(record))

    for route, recipients in routes_cfg.items():
        route_notified = set(notified.get(route, []))
        new_records = [r for r in matches_by_route[route] if alert_hash(r) not in route_notified]

        if new_records:
            subject = f"School Bus Alert - Route {route}"
            body = format_alert_body(route, new_records, url)
            log.info("Route %s: %d new alert(s); notifying %s", route, len(new_records), recipients)
            if args.dry_run:
                log.info("[dry-run] would send email:\n%s", body)
            else:
                send_email(config.get("mail", {}), recipients, subject, body)
        else:
            log.info("Route %s: no new alerts (%d active)", route, len(matches_by_route[route]))

        notified[route] = sorted(current_hashes_by_route[route])

    if not args.dry_run:
        save_json(state_path, state)

    log.info("Done.")


if __name__ == "__main__":
    main()
