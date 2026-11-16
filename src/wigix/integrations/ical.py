"""Calendar from an iCalendar (.ics) feed: a private Google/Nextcloud/Fastmail URL or a local file.

    integrations:
      cal:
        type: ical
        url_file: ~/.config/wigix/cal_url      # the secret address lives in a 0600 file (or `url:` / $ICAL_URL)
        # file: ~/calendar.ics                    # instead of a URL
        days: 7                                   # how far ahead to look (1 to 366)
        # timezone: Europe/Paris                  # for floating times and display; default: this machine's zone

``data`` is a list sorted by start: ``{title, start, end, all_day, location}``. ``start``/``end`` are
timezone-aware datetimes in the configured zone; for all-day events ``end`` is the (exclusive) midnight after
the last day. Recurring events are expanded (at most ``MAX_EVENTS`` occurrences per poll), events that already
ended today are dropped.
"""
import datetime as dt
import itertools
import pathlib
import zoneinfo

import icalendar
import recurring_ical_events
import requests

from . import integration
from .polling import PollingIntegration
from ..render import read_secret


MAX_DAYS = 366
MAX_EVENTS = 500              # occurrences read per poll: a hostile RRULE cannot explode the expansion


@integration("ical")
class ICal(PollingIntegration):
    interval = 300.0

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        tz = cfg.get("timezone")
        self.tz = zoneinfo.ZoneInfo(tz) if tz else dt.datetime.now().astimezone().tzinfo

    def _now(self) -> dt.datetime:
        return dt.datetime.now(self.tz)

    def _fetch(self) -> bytes:
        if self.cfg.get("file"):
            return pathlib.Path(self.cfg["file"]).expanduser().read_bytes()
        url = read_secret(self.cfg.get("url"), self.cfg.get("url_file"), "ICAL_URL")
        if not url:
            raise RuntimeError("no calendar configured (set url, url_file or file)")
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            # The feed address is a secret, and requests puts it in its messages: report only the kind of failure.
            status = getattr(e.response, "status_code", None)
            raise RuntimeError(f"calendar download failed ({status or type(e).__name__})") from None
        return r.content

    def _local(self, v) -> tuple[dt.datetime, bool]:
        """A DTSTART/DTEND value (date or datetime, aware or floating) -> (aware datetime in our zone, all_day)."""
        if isinstance(v, dt.datetime):
            return (v.replace(tzinfo=self.tz) if v.tzinfo is None else v.astimezone(self.tz)), False
        return dt.datetime.combine(v, dt.time.min, self.tz), True

    def poll(self):
        cal = icalendar.Calendar.from_ical(self._fetch())
        now = self._now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            days = int(self.cfg.get("days", 7))
        except (TypeError, ValueError):
            days = 7
        horizon = today + dt.timedelta(days=max(1, min(days, MAX_DAYS)))
        events = []
        # after() is lazy and ordered by start, so a runaway recurrence is cut off after MAX_EVENTS occurrences.
        for ev in itertools.islice(recurring_ical_events.of(cal).after(today), MAX_EVENTS):
            start, all_day = self._local(ev.start)
            if start >= horizon:
                break
            end = self._local(ev.end)[0] if ev.get("DTEND") or ev.get("DURATION") else start
            if not all_day and end < now:
                continue                                  # already over today
            events.append({"title": str(ev.get("SUMMARY", "")) or "(no title)", "start": start, "end": end,
                           "all_day": all_day, "location": str(ev.get("LOCATION", "") or "")})
        events.sort(key=lambda e: (e["start"], e["title"]))
        return events
