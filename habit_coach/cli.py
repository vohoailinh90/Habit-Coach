"""Command line: log what happened, see today, report, review, export the calendar, nudge."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from . import __version__, logs
from .calendar_export import build_events, events_as_json, render_ics
from .config import ROOT, ConfigError, current_time, iso_week_label, load_profile, logical_date
from .i18n import Translator
from .logs import KINDS, Event, event_id
from .nudge import FORCEABLE_KINDS, pick_nudge
from .plan import PlanVersionError, check_warnings, dump_plan, parse_time, write_version, with_habits
from .render import bar, pct, render_review, render_today, render_week, result_line, review_due_day, write_review_file
from .review import apply_proposals, build_review
from .scoring import summarize
from .tracker import Tracker

DEFAULT_ICS = Path("calendar") / "habit-coach.ics"
MAX_EXPORT_WEEKS = 52


class UsageError(Exception):
    """The command line is wrong in a way argparse cannot see; the message is already translated."""


def _early_lang(argv: Sequence[str]) -> str | None:
    probe = argparse.ArgumentParser(add_help=False)
    probe.add_argument("--lang")
    return probe.parse_known_args(list(argv))[0].lang


def build_parser(tr: Translator) -> argparse.ArgumentParser:
    t = tr.t
    parser = argparse.ArgumentParser(prog="habit.py", description=t("cli.description"), add_help=False)
    parser.add_argument("-h", "--help", action="help", help=t("arg.help"))
    parser.add_argument("--lang", choices=tr.locales, default=None, help=t("arg.lang"))
    parser.add_argument("--version", action="version", version=f"habit-coach {__version__}", help=t("arg.version"))
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    def command(name: str, **options):
        parser_ = sub.add_parser(name, help=t(f"cmd.{name}"), description=t(f"cmd.{name}"), add_help=False, **options)
        parser_.add_argument("-h", "--help", action="help", help=t("arg.help"))
        return parser_

    p = command("log")
    p.add_argument("text", nargs="*", help=t("arg.text"))
    p.add_argument("--habit", help=t("arg.habit"))
    p.add_argument("--kind", choices=KINDS, help=t("arg.kind"))
    p.add_argument("--at", metavar="HH:MM", help=t("arg.at"))
    p.add_argument("--date", metavar="YYYY-MM-DD", help=t("arg.date"))
    p.add_argument("--tiny", action="store_true", help=t("arg.tiny"))
    p.add_argument("--excused", action="store_true", help=t("arg.excused"))
    p.add_argument("--json", action="store_true", help=t("arg.json"))

    p = command("void")
    p.add_argument("id", help=t("arg.id"))
    p.add_argument("--date", metavar="YYYY-MM-DD", help=t("arg.date"))

    p = command("today")
    p.add_argument("--json", action="store_true", help=t("arg.json"))

    p = command("report")
    p.add_argument("--week", metavar="YYYY-Www", help=t("arg.week"))
    p.add_argument("--date", metavar="YYYY-MM-DD", help=t("arg.date"))
    p.add_argument("--json", action="store_true", help=t("arg.json"))

    p = command("review")
    p.add_argument("--week", metavar="YYYY-Www", help=t("arg.week"))
    p.add_argument("--date", metavar="YYYY-MM-DD", help=t("arg.date"))
    p.add_argument("--write", action="store_true", help=t("arg.write"))
    p.add_argument("--apply", action="store_true", help=t("arg.apply"))
    p.add_argument("--json", action="store_true", help=t("arg.json"))

    p = command("plan")
    plan_sub = p.add_subparsers(dest="plan_command", metavar="<show|check|new-version>")
    for name in ("show", "check", "new-version"):
        nested = plan_sub.add_parser(name, help=t(f"cmd.plan_{name.replace('-', '_')}"), add_help=False)
        nested.add_argument("-h", "--help", action="help", help=t("arg.help"))
        if name == "new-version":
            nested.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD", required=True, help=t("arg.from"))

    p = command("ics")
    p.add_argument("--from", dest="from_date", metavar="YYYY-MM-DD", help=t("arg.from"))
    p.add_argument("--weeks", type=int, help=t("arg.weeks"))
    p.add_argument("--out", help=t("arg.out"))
    p.add_argument("--format", choices=("ics", "json"), default="ics", help=t("arg.format"))

    p = command("nudge")
    p.add_argument("--moment", choices=FORCEABLE_KINDS, help=t("arg.moment"))
    p.add_argument("--json", action="store_true", help=t("arg.json"))
    return parser


MIN_YEAR, MAX_YEAR = 2000, 2100      # a typo like 0001-01-01 is an error, not an OverflowError three calls deep


def _parse_day(tr: Translator, value: str) -> date:
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise UsageError(tr.t("err.bad_date", value=value)) from None
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise UsageError(tr.t("err.bad_date", value=value))
    return day


def _parse_week(tr: Translator, value: str) -> date:
    try:
        year, week = value.upper().split("-W")
        day = date.fromisocalendar(int(year), int(week), 1)
    except ValueError:
        raise UsageError(tr.t("err.bad_week", value=value)) from None
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise UsageError(tr.t("err.bad_week", value=value))
    return day


def _emit(out: TextIO, lines: list[str]) -> None:
    out.write("\n".join(lines) + "\n")


def _json(out: TextIO, data: object) -> None:
    out.write(json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n")


def _result_json(r) -> dict:
    return {
        "habit": r.habit.id, "name": r.habit.name, "planned": r.planned.isoformat(), "status": r.status,
        "score": round(r.score, 3), "counted": r.counted, "scored": r.habit.scored,
        "actual": r.actual.isoformat() if r.actual else None, "offset_min": r.offset_min, "tiny": r.tiny,
    }


def cmd_log(a, tr, tracker, now, out) -> int:
    profile = tracker.profile
    text = " ".join(a.text).strip()
    kind = a.kind or ("start" if a.habit else "note")
    if kind in ("start", "done", "skip") and not a.habit:
        raise UsageError(tr.t("err.habit_required", kind=kind))
    if a.tiny and kind not in ("start", "done"):
        raise UsageError(tr.t("err.tiny_kind"))
    if a.excused and kind != "skip":
        raise UsageError(tr.t("err.excused_kind"))
    if kind == "note" and not text:
        raise UsageError(tr.t("err.empty_note"))

    if a.at:
        hhmm = parse_time(a.at)
        if hhmm is None:
            raise UsageError(tr.t("err.bad_time", value=a.at))
        small_hours = hhmm.hour < profile.day_cutoff_hour
        if a.date:
            ts = datetime.combine(_parse_day(tr, a.date), hhmm, tzinfo=profile.tz)
            if small_hours:
                ts += timedelta(days=1)         # 00:30 on a given logical day is the small hours after it
        elif small_hours:
            # "I went to bed at 00:30", said at 10:00: the most recent 00:30, which belongs to the evening before
            ts = datetime.combine(now.date(), hhmm, tzinfo=profile.tz)
            if ts > now + timedelta(minutes=5):
                ts -= timedelta(days=1)
        else:
            ts = datetime.combine(tracker.today(now), hhmm, tzinfo=profile.tz)
    elif a.date:
        raise UsageError(tr.t("err.date_needs_time"))
    else:
        ts = now.replace(second=0, microsecond=0)
    if ts > now + timedelta(minutes=5):
        raise UsageError(tr.t("err.future_time", value=f"{ts:%Y-%m-%d %H:%M}"))

    day = logical_date(ts, profile)
    if a.habit:
        plan = tracker.plan_on(day)
        if plan is None or plan.habit(a.habit) is None:
            ids = ", ".join(h.id for h in plan.habits) if plan else "-"
            raise UsageError(tr.t("err.unknown_habit", habit=a.habit, habits=ids))
    event = Event(event_id(ts, kind, a.habit, text, now), ts, kind, a.habit, text, a.tiny, a.excused, now)
    twin = next((e for e in tracker.events(day) if (e.ts, e.kind, e.habit, e.text, e.tiny, e.excused)
                 == (ts, kind, a.habit, text, a.tiny, a.excused)), None)
    if twin is None:
        logs.append_event(tracker.root, profile, event)
        tracker.forget(day)
    else:
        event = twin                                  # a retry or a double send: keep one entry

    result = next((r for r in tracker.day(day, now) if r.habit.id == a.habit), None) if a.habit else None
    summary = summarize(tracker.day(day, now))
    if a.json:
        _json(out, {
            "event": {"id": event.id, "ts": ts.isoformat(), "kind": kind, "habit": a.habit, "text": text, "duplicate": twin is not None},
            "result": _result_json(result) if result else None,
            "day_percent": None if summary.percent is None else round(summary.percent, 1),
            "streak": tracker.streak(a.habit, now) if a.habit else None,
        })
        return 0
    lines = [tr.t("log.duplicate" if twin else "log.saved", id=event.id, time=f"{ts:%H:%M}", text=text or kind)]
    if result:
        lines.append(result_line(tr, result, now))
        streak = tracker.streak(a.habit, now)
        if streak >= 2:
            lines.append(tr.t("today.streak_item", habit=result.habit.name, count=streak))
    if summary.percent is not None:
        lines.append(tr.t("today.progress", pct=pct(summary.percent), done=summary.done, total=summary.counted + summary.pending, bar=bar(summary.percent)))
    _emit(out, lines)
    return 0


def cmd_void(a, tr, tracker, now, out) -> int:
    around = _parse_day(tr, a.date) if a.date else tracker.today(now)
    found = logs.find_event(tracker.root, tracker.profile, a.id, around)
    if found is None:
        raise UsageError(tr.t("err.no_such_event", id=a.id))
    day, event = found
    logs.append_void(tracker.root, tracker.profile, day, event.id, now)
    out.write(tr.t("void.done", id=event.id, text=event.text or event.kind) + "\n")
    return 0


def cmd_today(a, tr, tracker, now, out) -> int:
    if a.json:
        today = tracker.today(now)
        results = tracker.day(today, now)
        summary = summarize(results)
        _json(out, {
            "date": today.isoformat(), "now": now.isoformat(),
            "percent": None if summary.percent is None else round(summary.percent, 1),
            "occurrences": [_result_json(r) | {"streak": tracker.streak(r.habit.id, now)} for r in results],
            "events": [{"id": e.id, "ts": e.ts.isoformat(), "kind": e.kind, "habit": e.habit, "text": e.text} for e in tracker.events(today)],
        })
        return 0
    lines = render_today(tr, tracker, now)
    lines.append("")
    lines.append(pick_nudge(tracker, now, tr).text)
    _emit(out, lines)
    return 0


def _target_day(a, tr, tracker, now) -> date:
    if a.week:
        return _parse_week(tr, a.week)
    return _parse_day(tr, a.date) if a.date else tracker.today(now)


def cmd_report(a, tr, tracker, now, out) -> int:
    week = tracker.week(_target_day(a, tr, tracker, now), now)
    previous = tracker.week(week.start - timedelta(days=7), now)
    if a.json:
        _json(out, {
            "week": iso_week_label(week.start), "start": week.start.isoformat(), "end": week.end.isoformat(),
            "percent": None if week.summary.percent is None else round(week.summary.percent, 1),
            "previous_percent": None if previous.summary.percent is None else round(previous.summary.percent, 1),
            "done": week.summary.done, "counted": week.summary.counted, "pending": week.summary.pending,
            "habits": {k: {"name": s.name, "planned": s.planned, "done": s.done, "on_time": s.on_time,
                           "score_pct": None if s.score_pct is None else round(s.score_pct, 1),
                           "median_offset_min": s.median_offset} for k, s in week.stats.items()},
            "occurrences": [_result_json(r) for r in week.results],
        })
        return 0
    _emit(out, render_week(tr, tracker, week, previous, now))
    return 0


def cmd_review(a, tr, tracker, now, out) -> int:
    target = None if a.week or a.date else review_due_day(tracker, now)     # no week given: the week whose review is owed
    review = build_review(tracker, target or _target_day(a, tr, tracker, now), now)
    label = iso_week_label(review.week.start)
    lines = render_review(tr, tracker, review, now)
    if a.json:
        _json(out, {
            "week": label, "percent": None if review.week.summary.percent is None else round(review.week.summary.percent, 1),
            "next_effective": review.next_effective.isoformat(), "reward": review.reward,
            "proposals": [p.__dict__ for p in review.proposals], "deferred": [p.__dict__ for p in review.deferred],
        })
    else:
        _emit(out, lines)
    if a.write:
        path = tracker.root / "reviews" / f"{label}.md"
        write_review_file(path, ["# " + lines[0], *lines[1:]], tr.t("review.notes_placeholder"))
        out.write(tr.t("review.written", path=str(path.relative_to(tracker.root))) + "\n")
    if a.apply:
        plan = tracker.plan_on(review.week.end)
        if plan is None:
            raise UsageError(tr.t("err.no_plan"))
        changing = [p for p in review.proposals if p.changes_plan]
        if not changing:
            out.write(tr.t("review.nothing_to_apply") + "\n")
            return 0
        updated = apply_proposals(plan, review.proposals, review.next_effective)
        try:
            write_version(tracker.root, updated)
        except PlanVersionError as exc:
            raise UsageError(tr.t("err.version_not_later", current=exc.current.isoformat(), requested=exc.requested.isoformat())) from None
        out.write(tr.t("review.applied", count=len(changing), date=review.next_effective.isoformat()) + "\n")
    return 0


def cmd_plan(a, tr, tracker, now, out) -> int:
    versions = tracker.versions
    if a.plan_command == "show" or a.plan_command is None:
        current = versions[-1] if versions else None
        out.write(dump_plan(current) if current else tr.t("today.no_plan") + "\n")
        return 0
    if a.plan_command == "check":
        if not versions:
            out.write(tr.t("plan.check_empty") + "\n")
            return 0
        warnings = [tr.t(key, **params) for key, params in check_warnings(versions[-1])]
        out.write(tr.t("plan.check_ok", count=len(versions[-1].habits), date=versions[-1].effective_from.isoformat()) + "\n")
        for line in warnings:
            out.write(f"- {line}\n")
        return 0
    start = _parse_day(tr, a.from_date)
    if not versions:
        raise UsageError(tr.t("err.no_plan"))
    try:
        path = write_version(tracker.root, with_habits(versions[-1], versions[-1].habits, start))
    except PlanVersionError as exc:
        raise UsageError(tr.t("err.version_not_later", current=exc.current.isoformat(), requested=exc.requested.isoformat())) from None
    out.write(tr.t("plan.new_version", path=str(path.relative_to(tracker.root)), date=start.isoformat()) + "\n")
    return 0


def cmd_ics(a, tr, tracker, now, out) -> int:
    profile = tracker.profile
    first = _parse_day(tr, a.from_date) if a.from_date else tracker.today(now)
    weeks = profile.weeks_ahead if a.weeks is None else a.weeks
    if not 1 <= weeks <= MAX_EXPORT_WEEKS:
        raise UsageError(tr.t("err.bad_weeks", max=MAX_EXPORT_WEEKS))
    events = build_events(tracker.versions, first, first + timedelta(days=7 * weeks - 1), profile, tr)
    if a.format == "json":
        _json(out, events_as_json(events, profile))
        return 0
    target = Path(a.out) if a.out else tracker.root / DEFAULT_ICS
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_ics(events, profile.calendar_name, now), encoding="utf-8", newline="")
    out.write(tr.t("ics.written", count=len(events), path=str(target), first=first.isoformat(), weeks=weeks) + "\n")
    return 0


def cmd_nudge(a, tr, tracker, now, out) -> int:
    nudge = pick_nudge(tracker, now, tr, a.moment)
    if a.json:
        _json(out, {"kind": nudge.kind, "habit": nudge.habit_id, "text": nudge.text})
    else:
        out.write(nudge.text + "\n")
    return 0


COMMANDS = {
    "log": cmd_log, "void": cmd_void, "today": cmd_today, "report": cmd_report,
    "review": cmd_review, "plan": cmd_plan, "ics": cmd_ics, "nudge": cmd_nudge,
}


def main(argv: Sequence[str] | None = None, *, root: Path = ROOT, env: Mapping[str, str] | None = None,
         out: TextIO | None = None, err: TextIO | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    tr = None
    out = out or sys.stdout
    err = err or sys.stderr
    try:
        profile = load_profile(root)
        # --lang, then $APP_LANG, then the owner's saved language (config/profile.yaml)
        requested = _early_lang(argv) or (os.environ if env is None else env).get("APP_LANG") or profile.language
        tr = Translator.from_dir(Path(__file__).parent / "locales", requested, default=profile.language)
        parser = build_parser(tr)
        args = parser.parse_args(argv)
        if not args.command:
            parser.print_help(out)
            return 0
        now = current_time(profile, env)
        tracker = Tracker(root, profile)
        return COMMANDS[args.command](args, tr, tracker, now, out)
    except UsageError as exc:
        err.write(f"{tr.t('err.prefix') if tr else 'error'}: {exc}\n")
        return 2
    except ConfigError as exc:
        err.write(f"error: {exc}\n")
        return 1
