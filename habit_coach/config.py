"""Repository paths, the owner's profile (config/profile.yaml) and the clock.

Everything time-related goes through here so that "what day is it" has one
answer: the owner's timezone, with a day that ends at `day_cutoff_hour` rather
than at midnight (a 00:30 bedtime still belongs to the evening before).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, fields
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

ROOT = Path(__file__).resolve().parents[1]
INT_TAG = "tag:yaml.org,2002:int"


class ConfigError(ValueError):
    """A config, plan or log file that cannot be used; the message says why."""


class _Loader(yaml.SafeLoader):
    """SafeLoader that reads `10:30` as the string it is, not as 630 (YAML 1.1 sexagesimal)."""


_Loader.yaml_implicit_resolvers = {
    first: [(tag, rx) for tag, rx in resolvers if tag != INT_TAG]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(INT_TAG, re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$"), list("-+0123456789"))


def load_yaml(path: Path) -> object:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc


@dataclass(frozen=True)
class Profile:
    language: str = "vi"
    timezone: str = "Asia/Tokyo"
    day_cutoff_hour: int = 4
    max_plan_changes_per_week: int = 3
    reward_pct: int = 80
    struggling_pct: int = 60
    reminder_min: int = 10
    weeks_ahead: int = 2
    calendar_name: str = "Habit Coach"
    identity: str = ""
    why: str = ""
    rewards: tuple[str, ...] = ()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)


_INT_RANGES = {
    "day_cutoff_hour": (0, 11),
    "max_plan_changes_per_week": (1, 10),
    "reward_pct": (1, 100),
    "struggling_pct": (1, 100),
    "reminder_min": (0, 1440),
    "weeks_ahead": (1, 8),
}


def load_profile(root: Path = ROOT) -> Profile:
    path = root / "config" / "profile.yaml"
    raw = load_yaml(path) if path.exists() else {}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: expected a mapping of settings")
    unknown = sorted(set(raw) - {f.name for f in fields(Profile)})
    if unknown:
        raise ConfigError(f"{path}: unknown setting(s): {', '.join(unknown)}")
    values = dict(raw)
    if "rewards" in values:
        values["rewards"] = tuple(str(item) for item in (values["rewards"] or []))
    for key in ("language", "timezone", "calendar_name", "identity", "why"):
        if key in values:
            values[key] = "" if values[key] is None else str(values[key])
    profile = Profile(**values)
    for key, (low, high) in _INT_RANGES.items():
        value = getattr(profile, key)
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ConfigError(f"{path}: {key} must be an integer from {low} to {high}, got {value!r}")
    try:
        profile.tz
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"{path}: unknown timezone {profile.timezone!r}") from exc
    return profile


def current_time(profile: Profile, env: Mapping[str, str] | None = None) -> datetime:
    """Now in the owner's timezone. `HABIT_NOW` (ISO 8601) pins it for tests and replays."""
    env = os.environ if env is None else env
    forced = env.get("HABIT_NOW")
    if forced:
        try:
            moment = datetime.fromisoformat(forced)
        except ValueError as exc:
            raise ConfigError(f"HABIT_NOW is not an ISO 8601 timestamp: {forced!r}") from exc
        return moment.replace(tzinfo=profile.tz) if moment.tzinfo is None else moment.astimezone(profile.tz)
    return datetime.now(profile.tz)


def logical_date(moment: datetime, profile: Profile) -> date:
    """The day a moment belongs to: 00:30 on the 6th is still the 5th when the cutoff is 04:00."""
    return (moment.astimezone(profile.tz) - timedelta(hours=profile.day_cutoff_hour)).date()


def day_bounds(day: date, profile: Profile) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time(profile.day_cutoff_hour), tzinfo=profile.tz)
    return start, start + timedelta(days=1)


def week_bounds(day: date) -> tuple[date, date]:
    """Monday and Sunday of the ISO week holding `day`."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def iso_week_label(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"
