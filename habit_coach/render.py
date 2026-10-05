"""Text for people: today's view, the weekly report, the review. All wording comes from the catalogs."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from .config import iso_week_label
from .i18n import Translator, format_date
from .review import Proposal, Review
from .scoring import Result, summarize
from .tracker import Tracker, WeekResult

ICONS = {
    "on_time": "✅", "late": "🟡", "off_window": "🟠", "excused": "💤",
    "skipped": "⏭️", "missed": "❌", "pending": "⏳",
}
TINY_ICON = "🌱"
BAR_CELLS = 10


def bar(percent: float | None) -> str:
    filled = 0 if percent is None else max(0, min(BAR_CELLS, round(percent / 100 * BAR_CELLS)))
    return "█" * filled + "░" * (BAR_CELLS - filled)


def pct(value: float | None) -> str:
    return "–" if value is None else f"{round(value)}%"


def day_label(tr: Translator, day: date) -> str:
    return f"{tr.t(f'weekday.{day.weekday()}')}, {format_date(day, tr.locale, 'long')}"


def pending_phase(result: Result, now: datetime) -> str:
    minutes = (now - result.planned).total_seconds() / 60
    habit = result.habit
    if minutes < -habit.early_min:
        return "upcoming"
    if minutes <= habit.grace_min:
        return "due"
    return "overdue" if minutes <= habit.late_limit_min else "passed"


def result_line(tr: Translator, result: Result, now: datetime) -> str:
    icon = TINY_ICON if result.tiny and result.done else ICONS[result.status]
    when = f"{result.planned:%H:%M}"
    if result.status in ("on_time", "late", "off_window"):
        detail = tr.t(f"status.{result.status}", actual=f"{result.actual:%H:%M}", minutes=abs(result.offset_min or 0))
        if result.tiny:
            detail += " · " + tr.t("status.tiny")
    elif result.status == "pending":
        detail = tr.t(f"status.pending_{pending_phase(result, now)}")
    elif result.status == "skipped" and result.reason:
        detail = tr.t("status.skipped_reason", reason=result.reason)
    else:
        detail = tr.t(f"status.{result.status}")
    anchor = "" if result.habit.scored else " " + tr.t("status.anchor_mark")
    return f"{icon} {when} {result.habit.name}{anchor} — {detail}"


REVIEW_GRACE_DAYS = 3   # a review is still owed on the Monday to Wednesday after its Sunday


def review_done(path: Path) -> bool:
    """Written, with the narrative in place of the pending marker (a run that died after --write is not done)."""
    return path.exists() and NOTES_PENDING not in path.read_text(encoding="utf-8")


def review_due_day(tracker: Tracker, now: datetime) -> date | None:
    """The Sunday whose review is owed right now: today on a Sunday, or last Sunday for three days after."""
    today = tracker.today(now)
    if today.weekday() == 6:
        week_day = today
    elif today.weekday() < REVIEW_GRACE_DAYS:
        week_day = date.fromordinal(today.toordinal() - today.weekday() - 1)
    else:
        return None
    if tracker.plan_on(week_day) is None:
        return None
    return None if review_done(tracker.root / "reviews" / f"{iso_week_label(week_day)}.md") else week_day


def review_due(tracker: Tracker, now: datetime) -> str | None:
    """The same, as an ISO week label."""
    day = review_due_day(tracker, now)
    return iso_week_label(day) if day else None


def render_today(tr: Translator, tracker: Tracker, now: datetime) -> list[str]:
    today = tracker.today(now)
    lines = [f"📅 {day_label(tr, today)}"]
    results = tracker.day(today, now)
    if tracker.plan_on(today) is None or not tracker.plan_on(today).habits:
        lines.append(tr.t("today.no_plan"))
        return lines
    if not results:
        lines.append(tr.t("today.rest_day"))
    else:
        lines += [result_line(tr, r, now) for r in results]
        summary = summarize(results)
        lines.append(tr.t("today.progress", pct=pct(summary.percent), done=summary.done, total=summary.counted + summary.pending, bar=bar(summary.percent)))
        streaks = [(r.habit.name, tracker.streak(r.habit.id, now)) for r in results if r.habit.scored]
        streaks = [s for s in streaks if s[1] >= 2]
        if streaks:
            lines.append(tr.t("today.streaks", streaks=", ".join(tr.t("today.streak_item", habit=n, count=c) for n, c in streaks)))
    due = review_due(tracker, now)
    if due:
        lines.append(tr.t("today.review_due", week=due))
    return lines


def _week_summary_lines(tr: Translator, week: WeekResult, previous: WeekResult | None) -> list[str]:
    s = week.summary
    lines = [tr.t("report.title", week=iso_week_label(week.start), start=format_date(week.start, tr.locale, "short"), end=format_date(week.end, tr.locale, "short"))]
    if s.percent is None:
        lines.append(tr.t("report.no_data"))
        return lines
    lines.append(tr.t("report.overall", pct=pct(s.percent), bar=bar(s.percent)))
    if previous is not None and previous.summary.percent is not None:
        delta = round(s.percent - previous.summary.percent)
        lines.append(tr.t("report.vs_last", delta=f"{delta:+d}", last=pct(previous.summary.percent)))
    lines.append(tr.t("report.counts", done=s.done, counted=s.counted, on_time=pct(100 * (s.on_time_rate or 0)), pending=s.pending, excused=s.excused))
    return lines


def render_week(tr: Translator, tracker: Tracker, week: WeekResult, previous: WeekResult | None, now: datetime) -> list[str]:
    lines = _week_summary_lines(tr, week, previous)
    for habit_id, stats in week.stats.items():
        if not stats.planned:
            continue
        streak = tracker.streak(habit_id, now)
        tail = " · " + tr.t("today.streak_item", habit=stats.name, count=streak) if streak >= 2 else ""
        lines.append(tr.t("report.habit_line", habit=stats.name, done=stats.done, planned=stats.planned,
                          on_time=stats.on_time, score=pct(stats.score_pct)) + tail)
    return lines


def render_proposal(tr: Translator, p: Proposal) -> str:
    params = {**p.reason, "habit": p.habit_name, "old": p.old, "new": p.new}
    if p.kind == "drop_day":
        params["day"] = tr.t(f"weekday.{p.old}")
    return tr.t(f"review.proposal.{p.kind}", **params)


def render_review(tr: Translator, tracker: Tracker, review: Review, now: datetime) -> list[str]:
    lines = render_week(tr, tracker, review.week, review.previous, now)
    if review.best_streak:
        lines.append(tr.t("review.best_streak", habit=review.best_streak[0], count=review.best_streak[1]))
    if review.reward:
        lines.append(tr.t("review.reward", reward=review.reward))
    lines.append("")
    if review.proposals:
        lines.append(tr.t("review.proposals_title", date=format_date(review.next_effective, tr.locale, "long")))
        lines += [f"- {render_proposal(tr, p)}" for p in review.proposals]
    else:
        lines.append(tr.t("review.no_changes"))
    if review.deferred:
        lines.append(tr.t("review.deferred_title"))
        lines += [f"- {render_proposal(tr, p)}" for p in review.deferred]
    return lines


AUTO_START = "<!-- auto:start -->"
AUTO_END = "<!-- auto:end -->"
NOTES_PENDING = "<!-- narrative: pending (delete this line when the notes below are written) -->"


def write_review_file(path: Path, auto_lines: list[str], notes_placeholder: str) -> None:
    """Rewrite the generated block and keep whatever the model wrote below it."""
    block = f"{AUTO_START}\n" + "\n".join(auto_lines) + f"\n{AUTO_END}\n"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if AUTO_START in text and AUTO_END in text:
            head, rest = text.split(AUTO_START, 1)
            _, tail = rest.split(AUTO_END, 1)
            path.write_text(head + block.rstrip("\n") + tail, encoding="utf-8")
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(block + "\n" + NOTES_PENDING + "\n" + notes_placeholder + "\n", encoding="utf-8")
