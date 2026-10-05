"""Build a throwaway Habit Coach repository for a test."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from habit_coach import logs
from habit_coach.config import load_profile

TZ = ZoneInfo("Asia/Tokyo")

PROFILE = """\
language: en
timezone: Asia/Tokyo
day_cutoff_hour: 4
max_plan_changes_per_week: 3
reward_pct: 80
reminder_min: 10
weeks_ahead: 2
rewards: ["good coffee", "evening off"]
"""

PLAN = """\
effective_from: 2026-09-28
habits:
  - id: read
    name: Read
    days: weekdays
    start: "07:45"
    duration_min: 30
    weight: 2
    anchor: sit down on the train
    tiny: one page
  - id: walk
    name: Walk
    days: [mon, wed, fri]
    start: "19:30"
    duration_min: 30
    keystone: true
    anchor: after dinner
    tiny: five minutes round the block
  - id: train
    name: Board the train
    days: weekdays
    start: "07:40"
    duration_min: 40
    scored: false
"""


def make_repo(tmp_path: Path, plan: str = PLAN, profile: str = PROFILE) -> Path:
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "plan" / "history").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "profile.yaml").write_text(profile, encoding="utf-8")
    (tmp_path / "plan" / "current.yaml").write_text(plan, encoding="utf-8")
    return tmp_path


def ts(day: str, hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime.combine(date.fromisoformat(day), time(hour, minute), tzinfo=TZ)


def when(day: str, hhmm: str) -> datetime:
    return ts(day, hhmm)


def log(root: Path, day: str, hhmm: str, habit: str | None, kind: str = "start", **fields) -> logs.Event:
    moment = ts(day, hhmm)
    event = logs.Event(
        id=logs.event_id(moment, kind, habit, fields.get("text", ""), moment),
        ts=moment, kind=kind, habit=habit, logged_at=moment, **fields,
    )
    logs.append_event(root, load_profile(root), event)
    return event
