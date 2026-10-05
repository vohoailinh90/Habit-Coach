"""The weekly plan: validation, versions and the planned start of each occurrence.

`plan/current.yaml` is the newest version; every version it replaced sits in
`plan/history/<effective_from>.yaml`. A day is always scored against the version
in force on that day, so rewriting the plan on Sunday never rewrites last week's
score.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import yaml

from .config import ConfigError, Profile, load_yaml

DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_GROUPS = {
    "daily": tuple(range(7)),
    "weekdays": (0, 1, 2, 3, 4),
    "weekends": (5, 6),
}
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
HHMM = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
RECOMMENDED_MAX_SCORED = 5


class PlanVersionError(ConfigError):
    """A new plan version must start later than the current one."""

    def __init__(self, current: date, requested: date) -> None:
        self.current, self.requested = current, requested
        super().__init__(f"the current plan is already effective from {current}; a new version must start later (got {requested})")


class PlanError(ConfigError):
    """The plan file is invalid; `.problems` lists every reason, not just the first."""

    def __init__(self, source: str, problems: list[str]) -> None:
        self.problems = problems
        super().__init__(f"{source}: " + "; ".join(problems))


@dataclass(frozen=True)
class Habit:
    id: str
    name: str
    days: tuple[int, ...]
    start: time
    duration_min: int
    grace_min: int = 15          # still "on time" this long after the planned start
    early_min: int = 90          # ... and this long before it
    late_limit_min: int = 120    # after grace, up to here is "late"; beyond is "off window"
    weight: int = 1              # 1-3, how much it moves the weekly percentage
    keystone: bool = False       # the habit the rest of the day hangs on: protect it
    scored: bool = True          # False: an anchor (train, work start) tracked but not in the percentage
    anchor: str = ""             # the existing routine this one follows ("after I board the train")
    tiny: str = ""               # the two-minute version that still counts
    reminder_min: int | None = None


@dataclass(frozen=True)
class Plan:
    effective_from: date
    habits: tuple[Habit, ...]

    def habit(self, habit_id: str) -> Habit | None:
        return next((h for h in self.habits if h.id == habit_id), None)

    def scheduled(self, day: date) -> list[Habit]:
        return sorted((h for h in self.habits if day.weekday() in h.days), key=lambda h: h.start)


def parse_time(value: object) -> time | None:
    match = HHMM.match(value.strip()) if isinstance(value, str) else None
    return time(int(match.group(1)), int(match.group(2))) if match else None


def _days(value: object) -> tuple[int, ...] | None:
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not items:
        return None
    days: set[int] = set()
    for item in items:
        text = str(item).strip().lower()
        key = text if text in DAY_GROUPS else text[:3]
        if key in DAY_GROUPS:
            days.update(DAY_GROUPS[key])
        elif key in DAY_NAMES:
            days.add(DAY_NAMES.index(key))
        else:
            return None
    return tuple(sorted(days))


def _int_field(entry: dict, key: str, default: int | None, low: int, high: int, where: str, problems: list[str]) -> int | None:
    value = entry.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        problems.append(f"{where}: {key} must be an integer from {low} to {high}")
        return default
    return value


def parse_plan(raw: object, source: str) -> Plan:
    problems: list[str] = []
    if not isinstance(raw, dict):
        raise PlanError(source, ["expected a mapping with effective_from and habits"])
    effective = raw.get("effective_from")
    if isinstance(effective, str):
        try:
            effective = date.fromisoformat(effective)
        except ValueError:
            effective = None
    if not isinstance(effective, date) or isinstance(effective, datetime):
        problems.append("effective_from must be a date such as 2026-10-05")
        effective = date.min
    entries = raw.get("habits")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise PlanError(source, ["habits must be a list"])

    habits: list[Habit] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            problems.append(f"habit #{index}: expected a mapping")
            continue
        where = f"habit '{entry.get('id', f'#{index}')}'"
        habit_id = entry.get("id")
        if not isinstance(habit_id, str) or not SLUG.match(habit_id):
            problems.append(f"{where}: id must be a lowercase slug such as morning-read")
            continue
        if habit_id in seen:
            problems.append(f"{where}: duplicate id")
            continue
        seen.add(habit_id)
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{where}: name is required")
            name = habit_id
        days = _days(entry.get("days"))
        if days is None:
            problems.append(f"{where}: days must list mon..sun, or daily / weekdays / weekends")
            days = ()
        start = parse_time(entry.get("start"))
        if start is None:
            problems.append(f"{where}: start must be a quoted 24-hour time such as \"07:30\"")
            start = time(0, 0)
        duration = _int_field(entry, "duration_min", None, 1, 600, where, problems)
        if duration is None:
            problems.append(f"{where}: duration_min is required")
            duration = 1
        keystone, scored = entry.get("keystone", False), entry.get("scored", True)
        for flag, value in (("keystone", keystone), ("scored", scored)):
            if not isinstance(value, bool):
                problems.append(f"{where}: {flag} must be true or false")
        habits.append(Habit(
            id=habit_id,
            name=name.strip(),
            days=days,
            start=start,
            duration_min=duration,
            grace_min=_int_field(entry, "grace_min", 15, 0, 240, where, problems) or 0,
            early_min=_int_field(entry, "early_min", 90, 0, 720, where, problems) or 0,
            late_limit_min=_int_field(entry, "late_limit_min", 120, 0, 720, where, problems) or 0,
            weight=_int_field(entry, "weight", 1, 1, 3, where, problems) or 1,
            keystone=keystone is True,
            scored=scored is not False,
            anchor=str(entry.get("anchor") or "").strip(),
            tiny=str(entry.get("tiny") or "").strip(),
            reminder_min=_int_field(entry, "reminder_min", None, 0, 1440, where, problems),
        ))
    if problems:
        raise PlanError(source, problems)
    return Plan(effective, tuple(habits))


def plan_to_dict(plan: Plan) -> dict[str, Any]:
    """Plain data for YAML: defaults and empty fields are left out so the file stays readable."""
    defaults = Habit("x", "x", (), time(0, 0), 1)
    habits = []
    for h in plan.habits:
        entry: dict[str, Any] = {
            "id": h.id,
            "name": h.name,
            "days": [DAY_NAMES[d] for d in h.days],
            "start": h.start.strftime("%H:%M"),
            "duration_min": h.duration_min,
        }
        for key in ("grace_min", "early_min", "late_limit_min", "weight", "keystone", "scored", "anchor", "tiny", "reminder_min"):
            value = getattr(h, key)
            if value != getattr(defaults, key):
                entry[key] = value
        habits.append(entry)
    return {"effective_from": plan.effective_from.isoformat(), "habits": habits}


def dump_plan(plan: Plan) -> str:
    header = "# Weekly plan. Quote times (\"07:30\"). Prefer `python3 habit.py plan ...` and the Sunday review over hand edits.\n"
    return header + yaml.safe_dump(plan_to_dict(plan), allow_unicode=True, sort_keys=False)


def load_versions(root: Path) -> list[Plan]:
    paths = [root / "plan" / "current.yaml", *sorted((root / "plan" / "history").glob("*.yaml"))]
    plans = [parse_plan(load_yaml(p), str(p.relative_to(root))) for p in paths if p.exists()]
    plans.sort(key=lambda p: p.effective_from)
    for earlier, later in zip(plans, plans[1:]):
        if earlier.effective_from == later.effective_from:
            raise ConfigError(f"two plan versions share effective_from {later.effective_from}")
    return plans


def plan_for(versions: list[Plan], day: date) -> Plan | None:
    """The version in force on `day`, or None before the first plan existed."""
    return next((p for p in reversed(versions) if p.effective_from <= day), None)


def planned_start(habit: Habit, day: date, profile: Profile) -> datetime:
    """When the occurrence on logical `day` starts; a 00:30 bedtime planned for the 5th is on the 6th."""
    moment = datetime.combine(day, habit.start, tzinfo=profile.tz)
    return moment + timedelta(days=1) if habit.start.hour < profile.day_cutoff_hour else moment


def write_version(root: Path, plan: Plan) -> Path:
    """Make `plan` the current version, moving the one it replaces into plan/history/."""
    current = root / "plan" / "current.yaml"
    if current.exists():
        old = parse_plan(load_yaml(current), "plan/current.yaml")
        if plan.effective_from <= old.effective_from:
            raise PlanVersionError(old.effective_from, plan.effective_from)
        history = root / "plan" / "history"
        history.mkdir(parents=True, exist_ok=True)
        (history / f"{old.effective_from.isoformat()}.yaml").write_text(current.read_text(encoding="utf-8"), encoding="utf-8")
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_text(dump_plan(plan), encoding="utf-8")
    return current


def with_habits(plan: Plan, habits: tuple[Habit, ...], effective_from: date) -> Plan:
    return replace(plan, habits=habits, effective_from=effective_from)


def check_warnings(plan: Plan) -> list[tuple[str, dict[str, object]]]:
    """Advice rather than errors, as (catalog key, params): things that predict a plan nobody can keep."""
    scored = [h for h in plan.habits if h.scored]
    warnings: list[tuple[str, dict[str, object]]] = []
    if len(scored) > RECOMMENDED_MAX_SCORED:
        warnings.append(("plan.warn.too_many", {"count": len(scored), "max": RECOMMENDED_MAX_SCORED}))
    for h in scored:
        if not h.anchor:
            warnings.append(("plan.warn.no_anchor", {"habit": h.name}))
        if not h.tiny:
            warnings.append(("plan.warn.no_tiny", {"habit": h.name}))
    return warnings
