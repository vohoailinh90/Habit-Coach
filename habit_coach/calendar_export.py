"""The plan as calendar events: an .ics file for Google Calendar, or JSON for the connector.

Both come from one list of events so they cannot disagree. UIDs are stable
(`<habit>-<yyyymmdd>@habit-coach`), so importing a regenerated file updates the
events already in the calendar instead of duplicating them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from .config import Profile
from .i18n import Translator
from .plan import Plan, plan_for, planned_start

UID_DOMAIN = "habit-coach"


@dataclass(frozen=True)
class CalendarEvent:
    uid: str
    habit_id: str
    title: str
    start: datetime
    end: datetime
    description: str
    reminder_min: int


def build_events(versions: list[Plan], first: date, last: date, profile: Profile, tr: Translator) -> list[CalendarEvent]:
    """One event per scheduled occurrence from `first` to `last` inclusive, each from the plan version in force that day."""
    events = []
    day = first
    while day <= last:
        plan = plan_for(versions, day)
        for habit in (plan.scheduled(day) if plan else []):
            start = planned_start(habit, day, profile)
            lines = []
            if habit.anchor:
                lines.append(tr.t("calendar.anchor", anchor=habit.anchor))
            if habit.tiny:
                lines.append(tr.t("calendar.tiny", tiny=habit.tiny))
            if habit.scored:
                lines.append(tr.t("calendar.log_it"))
            events.append(CalendarEvent(
                uid=f"{habit.id}-{day:%Y%m%d}@{UID_DOMAIN}",
                habit_id=habit.id,
                title=habit.name,
                start=start,
                end=start + timedelta(minutes=habit.duration_min),
                description="\n".join(lines),
                reminder_min=profile.reminder_min if habit.reminder_min is None else habit.reminder_min,
            ))
        day += timedelta(days=1)
    return sorted(events, key=lambda e: e.start)


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\n").replace("\n", "\\n")


def fold(line: str) -> list[str]:
    """RFC 5545 line folding: at most 75 octets a line, never splitting a UTF-8 character."""
    pieces, current, size = [], "", 0
    for char in line:
        width = len(char.encode("utf-8"))
        limit = 75 if not pieces else 74     # continuation lines spend one octet on their leading space
        if size + width > limit:
            pieces.append(current)
            current, size = "", 0
        current += char
        size += width
    pieces.append(current)
    return [pieces[0]] + [" " + p for p in pieces[1:]]


def _utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def render_ics(events: list[CalendarEvent], calendar_name: str, stamp: datetime) -> str:
    sequence = int(stamp.timestamp() // 60)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Habit Coach//habit-coach//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(calendar_name)}",
    ]
    for event in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{event.uid}",
            f"DTSTAMP:{_utc(stamp)}",
            f"LAST-MODIFIED:{_utc(stamp)}",
            f"SEQUENCE:{sequence}",
            f"DTSTART:{_utc(event.start)}",
            f"DTEND:{_utc(event.end)}",
            f"SUMMARY:{_escape(event.title)}",
        ]
        if event.description:
            lines.append(f"DESCRIPTION:{_escape(event.description)}")
        lines += [
            "BEGIN:VALARM",
            f"TRIGGER:-PT{event.reminder_min}M",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_escape(event.title)}",
            "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(part for line in lines for part in fold(line)) + "\r\n"


def events_as_json(events: list[CalendarEvent], profile: Profile) -> list[dict]:
    return [
        {
            "uid": e.uid,
            "habit": e.habit_id,
            "title": e.title,
            "start": e.start.isoformat(),
            "end": e.end.isoformat(),
            "timezone": profile.timezone,
            "description": e.description,
            "reminder_min": e.reminder_min,
        }
        for e in events
    ]
