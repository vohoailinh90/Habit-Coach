from __future__ import annotations

from datetime import date, timedelta

import pytest

from habit_coach.config import ConfigError
from habit_coach.plan import load_versions, write_version
from habit_coach.review import apply_proposals, build_review
from habit_coach.tracker import Tracker
from tests.support.repo import PLAN, PROFILE, log, make_repo, ts

WEEK = "2026-09-28"
AFTER = ts("2026-10-05", "08:00")
WEEKDAYS = ("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02")


def week_of(monday: str, offset: int) -> list[str]:
    start = date.fromisoformat(monday) + timedelta(days=offset)
    return [(start + timedelta(days=i)).isoformat() for i in range(7)]


def kinds(review):
    return [(p.kind, p.habit_id) for p in review.proposals]


def test_a_failing_habit_is_made_smaller_before_it_is_made_rarer(tmp_path):
    root = make_repo(tmp_path)
    log(root, "2026-09-28", "07:45", "read")                        # 1 of 5
    review = build_review(Tracker(root), date(2026, 10, 4), AFTER)
    by_habit = {p.habit_id: p for p in review.proposals}
    assert by_habit["read"].kind == "reduce_duration" and (by_habit["read"].old, by_habit["read"].new) == (30, 15)
    assert by_habit["walk"].kind == "reduce_duration"               # 0 of 3, keystone: shrunk, never dropped
    assert kinds(review)[0][1] == "walk"                            # the worst rate comes first
    assert "train" not in by_habit                                   # anchors are never reduced


def test_a_small_failing_habit_loses_its_worst_day_but_a_keystone_is_only_flagged(tmp_path):
    plan = PLAN.replace("duration_min: 30\n    keystone: true", "duration_min: 15\n    keystone: false").replace(
        "days: [mon, wed, fri]", "days: [mon, wed, fri]")
    root = make_repo(tmp_path, plan=plan)
    log(root, "2026-09-28", "19:30", "walk")                          # Mon done, Wed and Fri missed
    review = build_review(Tracker(root), date(2026, 10, 4), AFTER)
    drop = next(p for p in review.proposals if p.habit_id == "walk")
    assert (drop.kind, drop.old) == ("drop_day", 4)                   # Friday: tie on misses, the later day goes

    keystone = make_repo(tmp_path / "k", plan=PLAN.replace("duration_min: 30\n    keystone: true", "duration_min: 15\n    keystone: true"))
    log(keystone, "2026-09-28", "19:30", "walk")
    flagged = next(p for p in build_review(Tracker(keystone), date(2026, 10, 4), AFTER).proposals if p.habit_id == "walk")
    assert flagged.kind == "flag" and not flagged.changes_plan


def test_a_habit_done_late_every_time_moves_to_when_it_really_happens(tmp_path):
    root = make_repo(tmp_path)
    for day, at in zip(WEEKDAYS[:4], ("08:25", "08:25", "08:30", "08:20")):
        log(root, day, at, "read")                                    # 4 of 5 done, median +40 min
    review = build_review(Tracker(root), date(2026, 10, 4), AFTER)
    shift = next(p for p in review.proposals if p.kind == "shift_time")
    assert (shift.habit_id, shift.old, shift.new) == ("read", "07:45", "08:25")


def test_drift_in_an_anchor_is_followed_too(tmp_path):
    root = make_repo(tmp_path)
    for day in WEEKDAYS[:4]:
        log(root, day, "08:05", "train")                              # planned 07:40, really 08:05
    shift = next(p for p in build_review(Tracker(root), date(2026, 10, 4), AFTER).proposals if p.habit_id == "train")
    assert (shift.kind, shift.new) == ("shift_time", "08:05")


def test_three_strong_weeks_raise_one_habit_by_five_minutes(tmp_path):
    root = make_repo(tmp_path, plan=PLAN.replace("2026-09-28", "2026-09-14"))
    for monday in ("2026-09-14", "2026-09-21", "2026-09-28"):
        for day in week_of(monday, 0)[:5]:
            log(root, day, "07:45", "read")
        for day in (week_of(monday, 0)[0], week_of(monday, 0)[2], week_of(monday, 0)[4]):
            log(root, day, "19:30", "walk")
    review = build_review(Tracker(root), date(2026, 10, 4), AFTER)
    assert [(p.kind, p.habit_id, p.old, p.new) for p in review.proposals] == [("level_up", "read", 30, 35)]   # one level-up per review
    assert review.week.summary.percent == pytest.approx(100.0)


def test_no_level_up_in_a_week_that_was_hard_overall(tmp_path):
    root = make_repo(tmp_path, plan=PLAN.replace("2026-09-28", "2026-09-14"))
    for monday in ("2026-09-14", "2026-09-21", "2026-09-28"):
        for day in week_of(monday, 0)[:5]:
            log(root, day, "07:45", "read")
    review = build_review(Tracker(root), date(2026, 10, 4), AFTER)      # walk was never done: 10/13 = 77% < 80
    assert not [p for p in review.proposals if p.kind == "level_up"]
    assert any(p.kind == "reduce_duration" and p.habit_id == "walk" for p in review.proposals)


def test_too_little_evidence_changes_nothing(tmp_path):
    root = make_repo(tmp_path, plan=PLAN.replace("2026-09-28", "2026-10-02"))   # the plan only exists on Friday
    review = build_review(Tracker(root), date(2026, 10, 4), AFTER)
    assert review.proposals == () and review.deferred == ()


def test_the_weekly_cap_defers_the_rest_but_never_hides_a_flag(tmp_path):
    root = make_repo(tmp_path, profile=PROFILE.replace("max_plan_changes_per_week: 3", "max_plan_changes_per_week: 1"))
    review = build_review(Tracker(root), date(2026, 10, 4), AFTER)
    assert len([p for p in review.proposals if p.changes_plan]) == 1
    assert len(review.deferred) == 1                                       # two reductions were due; one waits a week


def test_a_reward_is_earned_only_at_the_threshold(tmp_path):
    root = make_repo(tmp_path)
    for day in WEEKDAYS:
        log(root, day, "07:45", "read")
    for day in (WEEKDAYS[0], WEEKDAYS[2], WEEKDAYS[4]):
        log(root, day, "19:30", "walk")
    assert build_review(Tracker(root), date(2026, 10, 4), AFTER).reward in ("good coffee", "evening off")
    quiet = make_repo(tmp_path / "q")
    assert build_review(Tracker(quiet), date(2026, 10, 4), AFTER).reward is None


def test_applying_writes_next_weeks_plan_and_archives_this_weeks(tmp_path):
    root = make_repo(tmp_path)
    log(root, "2026-09-28", "07:45", "read")
    tracker = Tracker(root)
    review = build_review(tracker, date(2026, 10, 4), AFTER)
    updated = apply_proposals(tracker.plan_on(date(2026, 10, 4)), review.proposals, review.next_effective)
    write_version(root, updated)
    versions = load_versions(root)
    assert [v.effective_from for v in versions] == [date(2026, 9, 28), date(2026, 10, 5)]
    assert versions[1].habit("read").duration_min == 15 and versions[0].habit("read").duration_min == 30
    again = build_review(Tracker(root), date(2026, 10, 4), AFTER)             # last week is still judged by last week's plan
    assert again.week.stats["read"].planned == 5


def test_a_stale_proposal_is_refused_rather_than_guessed_at(tmp_path):
    root = make_repo(tmp_path)
    log(root, "2026-09-28", "07:45", "read")
    tracker = Tracker(root)
    review = build_review(tracker, date(2026, 10, 4), AFTER)
    plan = tracker.plan_on(date(2026, 10, 4))
    changed = type(plan)(plan.effective_from, tuple(
        h if h.id != "read" else type(h)(**{**h.__dict__, "duration_min": 20}) for h in plan.habits))
    with pytest.raises(ConfigError, match="duration is 20"):
        apply_proposals(changed, review.proposals, review.next_effective)


def test_exactly_half_done_is_not_a_failing_habit(tmp_path):
    plan = "effective_from: 2026-09-28\nhabits:\n  - {id: stretch, name: Stretch, days: [mon, tue, wed, thu], start: '07:00', duration_min: 20}\n"
    half = make_repo(tmp_path / "half", plan=plan)
    for day in ("2026-09-28", "2026-09-29"):
        log(half, day, "07:00", "stretch")                                  # 2 of 4
    assert not [p for p in build_review(Tracker(half), date(2026, 10, 4), AFTER).proposals if p.kind == "reduce_duration"]
    fewer = make_repo(tmp_path / "fewer", plan=plan)
    log(fewer, "2026-09-28", "07:00", "stretch")                            # 1 of 4
    assert [p.kind for p in build_review(Tracker(fewer), date(2026, 10, 4), AFTER).proposals] == ["reduce_duration"]


def test_small_drift_is_left_alone(tmp_path):
    root = make_repo(tmp_path)
    for day in WEEKDAYS[:4]:
        log(root, day, "07:55", "read")                                     # ten minutes late, four times: within a normal morning
    review = build_review(Tracker(root), date(2026, 10, 4), AFTER)
    assert not [p for p in review.proposals if p.kind == "shift_time" and p.habit_id == "read"]
