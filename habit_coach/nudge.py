"""Reminders and motivation: the right sentence for this moment, chosen by rule.

The choice is deterministic (same state, same minute, same sentence) so it is
testable; variety comes from rotating among VARIANTS sentences per kind, keyed by
the date. The wording lives in the catalogs under `nudge.<kind>.<n>`.

Kinds, in the order they are considered when none is asked for:

    comeback   a habit was missed yesterday and is due again today: "never miss twice"
    pre        a habit starts within the next 45 minutes (or is inside its window)
    milestone  today's completion made a streak reach 3, 7, 14, 30, 60 or 100
    done       something was logged in the last 30 minutes
    evening    after 20:00: the day's recap
    morning    before 11:00: today's plan and who the owner is becoming
    generic    anything else: the identity sentence and the reason why
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from .i18n import Translator
from .scoring import Result
from .tracker import Tracker

VARIANTS = 3
KINDS = ("comeback", "pre", "milestone", "done", "evening", "morning", "sunday", "generic")
# comeback, pre, milestone and done assert something about the day ("you missed yesterday"), so they are only
# ever chosen by rule; asking for one with nothing behind it would say something false.
FORCEABLE_KINDS = ("evening", "morning", "sunday", "generic")
MILESTONES = (3, 7, 14, 30, 60, 100)
PRE_WINDOW_MIN = 45
DONE_WINDOW_MIN = 30
EVENING_HOUR = 20
MORNING_HOUR = 11


@dataclass(frozen=True)
class Nudge:
    kind: str
    text: str
    habit_id: str | None = None


def _variant(*parts: object) -> int:
    digest = hashlib.sha1("|".join(map(str, parts)).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % VARIANTS


def _eta_min(result: Result, now: datetime) -> int:
    return round((result.planned - now).total_seconds() / 60)


def pick_nudge(tracker: Tracker, now: datetime, tr: Translator, kind: str | None = None) -> Nudge:
    profile = tracker.profile
    today = tracker.today(now)
    results = tracker.day(today, now)
    scored = [r for r in results if r.habit.scored]
    summary_percent = tracker.week(today, now).summary.percent if results else None
    identity = profile.identity or tr.t("nudge.identity_default")
    why = profile.why or tr.t("nudge.why_default")
    pending = [r for r in scored if r.status == "pending"]
    done_today = [r for r in scored if r.done]
    day_summary = [r for r in scored if r.counted]
    day_pct = round(100 * sum(r.habit.weight * r.score for r in day_summary) / sum(r.habit.weight for r in day_summary)) if day_summary else 0

    def build(chosen: str, target: Result | None = None, **extra: object) -> Nudge:
        habit = target.habit if target else None
        params = {
            "habit": habit.name if habit else "",
            "tiny": (habit.tiny or tr.t("nudge.tiny_default")) if habit else "",
            "anchor": habit.anchor if habit else "",
            "streak": tracker.streak(habit.id, now) if habit else 0,
            "pct": day_pct,
            "done": len(done_today),
            "total": len(scored),
            "remaining": len(pending),
            "identity": identity,
            "why": why,
        }
        params.update(extra)
        index = _variant(today, chosen, habit.id if habit else "")
        lines = [tr.t(f"nudge.{chosen}.{index}", **params)]
        if chosen == "pre" and habit and habit.anchor:
            lines.append(tr.t("nudge.anchor_line", anchor=habit.anchor))
        return Nudge(chosen, "\n".join(lines), habit.id if habit else None)

    if kind is None:
        yesterday = today - timedelta(days=1)
        missed_yesterday = {r.habit.id for r in tracker.day(yesterday, now) if r.habit.scored and r.status in ("missed", "skipped")}
        comeback = next((r for r in pending if r.habit.id in missed_yesterday), None)
        upcoming = next((r for r in sorted(pending, key=lambda r: r.planned) if -PRE_WINDOW_MIN <= _eta_min(r, now) <= PRE_WINDOW_MIN), None)
        milestone = next(
            (r for r in sorted(done_today, key=lambda r: r.actual or now, reverse=True) if tracker.streak(r.habit.id, now) in MILESTONES),
            None,
        )
        recent = next(
            (r for r in sorted(done_today, key=lambda r: r.actual or now, reverse=True)
             if r.actual and timedelta(0) <= now - r.actual <= timedelta(minutes=DONE_WINDOW_MIN)),
            None,
        )
        if comeback:
            return build("comeback", comeback)
        if upcoming:
            return build("pre", upcoming)
        if milestone:
            return build("milestone", milestone)
        if recent:
            return build("done", recent)
        if now.hour >= EVENING_HOUR and scored:
            return build("evening")
        if now.hour < MORNING_HOUR and scored:
            return build("morning")
        return build("generic")

    if kind not in KINDS:
        raise ValueError(f"unknown nudge kind {kind!r}; choose from {', '.join(KINDS)}")
    target = None
    if kind in ("pre", "comeback"):
        target = pending[0] if pending else (scored[0] if scored else None)
    elif kind in ("done", "milestone"):
        target = max(done_today, key=lambda r: r.actual or now, default=None)
    extra = {"pct": round(summary_percent)} if kind == "sunday" and summary_percent is not None else {}
    return build(kind, target, **extra)
