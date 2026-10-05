"""The Sunday review: from one week of data to a small, safe set of plan changes.

The rules are deliberately conservative and deterministic. The model writes the
narrative and decides what to say to the owner; it does not decide the numbers.

    reduce_duration  done < 50% of planned: make it smaller before making it rarer
    drop_day         still failing at <= 15 min: drop the day it fails on most (never a keystone)
    shift_time       completed but habitually off the planned start: move the plan to reality
    level_up         three straight strong weeks and a healthy week overall: +5 minutes, once
    flag             nothing left to cut: a conversation, not an automatic edit

At most `max_plan_changes_per_week` edits are proposed; change fatigue is how
plans die. Reductions come first, so a bad week gets easier before a good habit
gets harder.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta

from .config import ConfigError, Profile
from .plan import Plan, with_habits
from .tracker import Tracker, WeekResult

MIN_PLANNED = 2          # fewer counted occurrences than this is too little evidence to change anything
REDUCE_BELOW = 0.5       # done rate under this triggers a reduction
SMALL_DURATION = 15      # at or under this a habit is already small: cut days, not minutes
MIN_DURATION = 10
MIN_DAYS = 2
SHIFT_MIN_DONE = 3
SHIFT_MIN_OFFSET = 20    # minutes of habitual drift before the plan follows it
SHIFT_MAX_OFFSET = 120
LEVEL_UP_WEEKS = 3
LEVEL_UP_DONE = 0.9
LEVEL_UP_ON_TIME = 0.8
LEVEL_UP_MIN_OVERALL = 80
LEVEL_UP_STEP = 5


@dataclass(frozen=True)
class Proposal:
    kind: str                         # reduce_duration | drop_day | shift_time | level_up | flag
    habit_id: str
    habit_name: str
    old: object = None                # duration, weekday index or "HH:MM"
    new: object = None
    reason: dict[str, object] = field(default_factory=dict)   # params for the catalog sentence

    @property
    def changes_plan(self) -> bool:
        return self.kind != "flag"


@dataclass(frozen=True)
class Review:
    week: WeekResult
    previous: WeekResult
    proposals: tuple[Proposal, ...]
    deferred: tuple[Proposal, ...]
    next_effective: date
    reward: str | None
    best_streak: tuple[str, int] | None


def _round5(minutes: float) -> int:
    return int(5 * round(minutes / 5))


def _shifted(start: time, minutes: int) -> time:
    total = (start.hour * 60 + start.minute + minutes) % (24 * 60)
    return time(total // 60, total % 60)


def _strong(stats, planned_floor: int = MIN_PLANNED) -> bool:
    return (
        stats is not None
        and stats.planned >= planned_floor
        and (stats.done_rate or 0) >= LEVEL_UP_DONE
        and (stats.on_time_rate or 0) >= LEVEL_UP_ON_TIME
    )


def propose(plan: Plan, week: WeekResult, history: list[WeekResult], profile: Profile) -> tuple[list[Proposal], list[Proposal]]:
    reductions: list[tuple[float, int, Proposal]] = []
    shifts: list[Proposal] = []
    flags: list[Proposal] = []
    levels: list[Proposal] = []
    overall = week.summary.percent

    for habit in plan.habits:
        stats = week.stats.get(habit.id)
        if stats is None or stats.planned < MIN_PLANNED:
            continue
        rate = stats.done_rate or 0.0
        if habit.scored and rate < REDUCE_BELOW:
            reason = {"habit": habit.name, "done": stats.done, "planned": stats.planned}
            if habit.duration_min > SMALL_DURATION:
                new = max(MIN_DURATION, _round5(habit.duration_min / 2))
                proposal = Proposal("reduce_duration", habit.id, habit.name, habit.duration_min, new, reason)
            elif not habit.keystone and len(habit.days) > MIN_DAYS and stats.misses_by_weekday:
                worst = max(stats.misses_by_weekday, key=lambda d: (stats.misses_by_weekday[d], d))
                proposal = Proposal("drop_day", habit.id, habit.name, worst, None, reason)
            else:
                flags.append(Proposal("flag", habit.id, habit.name, reason=reason))
                continue
            reductions.append((rate, -habit.weight, proposal))
        elif stats.done >= SHIFT_MIN_DONE and stats.median_offset is not None and abs(stats.median_offset) >= SHIFT_MIN_OFFSET:
            shift = max(-SHIFT_MAX_OFFSET, min(SHIFT_MAX_OFFSET, _round5(stats.median_offset)))
            new_start = _shifted(habit.start, shift)
            if new_start != habit.start and (habit.start.hour < profile.day_cutoff_hour) == (new_start.hour < profile.day_cutoff_hour):
                shifts.append(Proposal(
                    "shift_time", habit.id, habit.name, habit.start.strftime("%H:%M"), new_start.strftime("%H:%M"),
                    {"habit": habit.name, "minutes": shift, "done": stats.done},
                ))
        elif habit.scored and overall is not None and overall >= LEVEL_UP_MIN_OVERALL and len(history) >= LEVEL_UP_WEEKS - 1:
            recent = [h.stats.get(habit.id) for h in history[: LEVEL_UP_WEEKS - 1]]
            if _strong(stats) and all(_strong(s) for s in recent):
                levels.append(Proposal(
                    "level_up", habit.id, habit.name, habit.duration_min, habit.duration_min + LEVEL_UP_STEP,
                    {"habit": habit.name, "weeks": LEVEL_UP_WEEKS},
                ))

    ordered = [p for _, _, p in sorted(reductions, key=lambda item: item[:2])] + shifts + levels[:1]
    cap = profile.max_plan_changes_per_week
    return ordered[:cap] + flags, ordered[cap:]


def build_review(tracker: Tracker, any_day: date, now: datetime) -> Review:
    week = tracker.week(any_day, now)
    history = [tracker.week(week.start - timedelta(days=7 * k), now) for k in (1, 2)]
    plan = tracker.plan_on(week.end) or Plan(week.end, ())
    proposals, deferred = propose(plan, week, history, tracker.profile)
    rewards = tracker.profile.rewards
    earned = week.summary.percent is not None and week.summary.percent >= tracker.profile.reward_pct
    reward = rewards[week.start.isocalendar()[1] % len(rewards)] if rewards and earned else None
    streaks = [(h.name, tracker.streak(h.id, now)) for h in plan.habits if h.scored]
    best = max(streaks, key=lambda item: item[1], default=None)
    return Review(
        week=week,
        previous=history[0],
        proposals=tuple(proposals),
        deferred=tuple(deferred),
        next_effective=week.start + timedelta(days=7),
        reward=reward,
        best_streak=best if best and best[1] > 0 else None,
    )


def apply_proposals(plan: Plan, proposals: tuple[Proposal, ...] | list[Proposal], effective_from: date) -> Plan:
    """The next week's plan. A proposal made against an older plan than the one given is refused, not guessed at."""
    habits = {h.id: h for h in plan.habits}
    for p in proposals:
        if not p.changes_plan:
            continue
        habit = habits.get(p.habit_id)
        if habit is None:
            raise ConfigError(f"proposal for '{p.habit_id}' refers to a habit that is no longer in the plan")
        if p.kind in ("reduce_duration", "level_up"):
            if habit.duration_min != p.old:
                raise ConfigError(f"'{p.habit_id}' duration is {habit.duration_min}, proposal expected {p.old}")
            habits[p.habit_id] = replace(habit, duration_min=int(p.new))
        elif p.kind == "drop_day":
            if p.old not in habit.days:
                raise ConfigError(f"'{p.habit_id}' no longer runs on weekday {p.old}")
            habits[p.habit_id] = replace(habit, days=tuple(d for d in habit.days if d != p.old))
        elif p.kind == "shift_time":
            if habit.start.strftime("%H:%M") != p.old:
                raise ConfigError(f"'{p.habit_id}' starts at {habit.start:%H:%M}, proposal expected {p.old}")
            hour, minute = str(p.new).split(":")
            habits[p.habit_id] = replace(habit, start=time(int(hour), int(minute)))
    return with_habits(plan, tuple(habits[h.id] for h in plan.habits), effective_from)
