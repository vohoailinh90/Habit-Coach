"""The repository as a whole: plan versions + logs -> day results, week results, streaks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import logs
from .config import Profile, load_profile, logical_date, week_bounds
from .logs import Event
from .plan import Plan, load_versions, plan_for
from .scoring import HabitStats, Result, Summary, habit_stats, score_day, summarize

STREAK_LOOKBACK_DAYS = 400


@dataclass(frozen=True)
class WeekResult:
    start: date                       # Monday
    end: date                         # Sunday
    results: tuple[Result, ...]
    summary: Summary
    stats: dict[str, HabitStats]


class Tracker:
    def __init__(self, root: Path, profile: Profile | None = None) -> None:
        self.root = root
        self.profile = profile or load_profile(root)
        self.versions: list[Plan] = load_versions(root)
        self._events: dict[date, list[Event]] = {}

    def plan_on(self, day: date) -> Plan | None:
        return plan_for(self.versions, day)

    def events(self, day: date) -> list[Event]:
        if day not in self._events:
            self._events[day] = logs.read_day(self.root, self.profile, day)
        return self._events[day]

    def forget(self, day: date) -> None:
        self._events.pop(day, None)

    def day(self, day: date, now: datetime) -> list[Result]:
        return score_day(self.plan_on(day), day, self.events(day), now, self.profile)

    def week(self, any_day: date, now: datetime) -> WeekResult:
        start, end = week_bounds(any_day)
        results: list[Result] = []
        for offset in range(7):
            results.extend(self.day(start + timedelta(days=offset), now))
        return WeekResult(start, end, tuple(results), summarize(results), habit_stats(results))

    def today(self, now: datetime) -> date:
        return logical_date(now, self.profile)

    def streak(self, habit_id: str, now: datetime) -> int:
        """Consecutive scheduled occurrences completed, newest first.

        Excused rest days and today's still-pending occurrence neither add to the
        streak nor break it; a skipped or missed occurrence ends it.
        """
        today = self.today(now)
        count = 0
        for back in range(STREAK_LOOKBACK_DAYS):
            day = today - timedelta(days=back)
            plan = self.plan_on(day)
            if plan is None:
                break
            result = next((r for r in self.day(day, now) if r.habit.id == habit_id), None)
            if result is None:
                continue
            if result.done:
                count += 1
            elif result.status in ("excused", "pending"):
                continue
            else:
                break
        return count
