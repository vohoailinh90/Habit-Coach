"""Plan versus reality: the adherence score. Pure functions, no I/O, no model.

One occurrence of a habit on one day resolves to exactly one status:

    on_time     started inside [planned - early, planned + grace]      1.0
    late        started after grace, up to late_limit                  0.7
    off_window  started that day, but outside both windows             0.4
    excused     skipped for a reason that is rest, not a lapse         not counted
    skipped     skipped without that excuse                            0
    missed      nothing logged and the day is over                     0
    pending     nothing logged yet and the day is not over             not counted

Doing only the tiny version multiplies the timing score by TINY_FACTOR: it keeps
the chain alive without pretending it was the full habit.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Sequence

from .config import Profile, day_bounds, logical_date
from .logs import COMPLETION_KINDS, Event
from .plan import Habit, Plan, planned_start

TIMING_SCORE = {"on_time": 1.0, "late": 0.7, "off_window": 0.4}
TINY_FACTOR = 0.6
DONE_STATUSES = frozenset(TIMING_SCORE)


@dataclass(frozen=True)
class Result:
    habit: Habit
    day: date
    planned: datetime
    status: str
    score: float                      # 0..1; meaningful only when counted
    counted: bool                     # False for excused and pending
    actual: datetime | None = None
    offset_min: int | None = None     # actual - planned, in minutes (negative = early)
    tiny: bool = False
    reason: str = ""

    @property
    def done(self) -> bool:
        return self.status in DONE_STATUSES


def resolve(habit: Habit, day: date, events: Sequence[Event], now: datetime, profile: Profile) -> Result:
    planned = planned_start(habit, day, profile)
    mine = [e for e in events if e.habit == habit.id]
    completions = [e for e in mine if e.kind in COMPLETION_KINDS]
    if completions:
        first = min(completions, key=lambda e: e.ts)
        offset = round((first.ts - planned).total_seconds() / 60)
        if -habit.early_min <= offset <= habit.grace_min:
            status = "on_time"
        elif habit.grace_min < offset <= habit.late_limit_min:
            status = "late"
        else:
            status = "off_window"
        tiny = all(e.tiny for e in completions)
        score = TIMING_SCORE[status] * (TINY_FACTOR if tiny else 1.0)
        return Result(habit, day, planned, status, score, True, first.ts, offset, tiny)
    skips = [e for e in mine if e.kind == "skip"]
    if skips:
        excused = any(e.excused for e in skips)
        reason = next((e.text for e in skips if e.text), "")
        return Result(habit, day, planned, "excused" if excused else "skipped", 0.0, not excused, reason=reason)
    if now >= day_bounds(day, profile)[1]:
        return Result(habit, day, planned, "missed", 0.0, True)
    return Result(habit, day, planned, "pending", 0.0, False)


def score_day(plan: Plan | None, day: date, events: Sequence[Event], now: datetime, profile: Profile) -> list[Result]:
    """Every occurrence scheduled on `day`; nothing for a day that has not started or predates the plan."""
    if plan is None or day > logical_date(now, profile):
        return []
    return [resolve(h, day, events, now, profile) for h in plan.scheduled(day)]


@dataclass(frozen=True)
class HabitStats:
    habit_id: str
    name: str
    planned: int                      # counted occurrences (excused and pending excluded)
    done: int
    on_time: int
    score_sum: float
    offsets: tuple[int, ...]          # minutes from the planned start, completed occurrences only
    misses_by_weekday: dict[int, int]

    @property
    def done_rate(self) -> float | None:
        return self.done / self.planned if self.planned else None

    @property
    def on_time_rate(self) -> float | None:
        return self.on_time / self.planned if self.planned else None

    @property
    def score_pct(self) -> float | None:
        return 100 * self.score_sum / self.planned if self.planned else None

    @property
    def median_offset(self) -> int | None:
        return round(statistics.median(self.offsets)) if self.offsets else None


@dataclass(frozen=True)
class Summary:
    percent: float | None             # weighted adherence over scored habits; None with nothing to count
    done_rate: float | None
    on_time_rate: float | None
    counted: int
    done: int
    pending: int
    excused: int


def habit_stats(results: Iterable[Result]) -> dict[str, HabitStats]:
    grouped: dict[str, list[Result]] = {}
    for r in results:
        grouped.setdefault(r.habit.id, []).append(r)
    stats = {}
    for habit_id, rows in grouped.items():
        counted = [r for r in rows if r.counted]
        misses: dict[int, int] = {}
        for r in counted:
            if not r.done:
                misses[r.day.weekday()] = misses.get(r.day.weekday(), 0) + 1
        stats[habit_id] = HabitStats(
            habit_id=habit_id,
            name=rows[-1].habit.name,
            planned=len(counted),
            done=sum(r.done for r in counted),
            on_time=sum(r.status == "on_time" for r in counted),
            score_sum=sum(r.score for r in counted),
            offsets=tuple(r.offset_min for r in rows if r.done and r.offset_min is not None),
            misses_by_weekday=misses,
        )
    return stats


def summarize(results: Iterable[Result]) -> Summary:
    rows = list(results)
    scored = [r for r in rows if r.habit.scored and r.counted]
    weight = sum(r.habit.weight for r in scored)
    percent = 100 * sum(r.habit.weight * r.score for r in scored) / weight if weight else None
    return Summary(
        percent=percent,
        done_rate=sum(r.done for r in scored) / len(scored) if scored else None,
        on_time_rate=sum(r.status == "on_time" for r in scored) / len(scored) if scored else None,
        counted=len(scored),
        done=sum(r.done for r in scored),
        pending=sum(r.status == "pending" and r.habit.scored for r in rows),
        excused=sum(r.status == "excused" and r.habit.scored for r in rows),
    )
