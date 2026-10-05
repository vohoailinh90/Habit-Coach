from __future__ import annotations

from datetime import date, time

import pytest

from habit_coach import logs
from habit_coach.config import ConfigError, load_profile, logical_date
from habit_coach.plan import PlanError, check_warnings, dump_plan, load_versions, parse_plan, plan_for, planned_start, write_version
from habit_coach.scoring import TINY_FACTOR, resolve, score_day, summarize
from habit_coach.tracker import Tracker
from tests.support.repo import log, make_repo, ts, when


# ---------- plan ----------

def test_plan_round_trips_and_keeps_defaults_out_of_the_file(tmp_path):
    root = make_repo(tmp_path)
    plan = load_versions(root)[0]
    again = parse_plan(load_yaml_text(dump_plan(plan)), "x")
    assert again == plan
    assert "grace_min" not in dump_plan(plan)          # defaults are not written out
    assert plan.habit("walk").keystone and not plan.habit("train").scored


def load_yaml_text(text):
    import yaml
    from habit_coach.config import _Loader
    return yaml.load(text, Loader=_Loader)


def test_unquoted_time_is_read_as_a_time_not_as_sexagesimal_minutes(tmp_path):
    root = make_repo(tmp_path, plan="effective_from: 2026-09-28\nhabits:\n  - {id: a, name: A, days: daily, start: 10:30, duration_min: 5}\n")
    assert load_versions(root)[0].habit("a").start == time(10, 30)


def test_every_problem_is_reported_at_once():
    raw = {"effective_from": "nope", "habits": [
        {"id": "Bad Id", "name": "x", "days": "daily", "start": "07:00", "duration_min": 5},
        {"id": "a", "name": "", "days": ["funday"], "start": "7pm", "duration_min": 0, "weight": 9},
    ]}
    with pytest.raises(PlanError) as caught:
        parse_plan(raw, "plan")
    text = str(caught.value)
    for fragment in ("effective_from", "slug", "name is required", "days must", "start must", "duration_min", "weight"):
        assert fragment in text


def test_duplicate_habit_ids_are_rejected():
    entry = {"id": "a", "name": "A", "days": "daily", "start": "07:00", "duration_min": 5}
    with pytest.raises(PlanError, match="duplicate id"):
        parse_plan({"effective_from": "2026-09-28", "habits": [entry, dict(entry)]}, "plan")


def test_day_groups_and_names():
    plan = parse_plan({"effective_from": "2026-09-28", "habits": [
        {"id": "a", "name": "A", "days": "weekdays", "start": "07:00", "duration_min": 5},
        {"id": "b", "name": "B", "days": ["Sat", "sunday"], "start": "07:00", "duration_min": 5},
    ]}, "plan")
    assert plan.habit("a").days == (0, 1, 2, 3, 4) and plan.habit("b").days == (5, 6)


def test_the_version_in_force_on_a_day_decides_the_plan(tmp_path):
    root = make_repo(tmp_path)
    later = parse_plan({"effective_from": "2026-10-05", "habits": []}, "plan")
    write_version(root, later)
    versions = load_versions(root)
    assert plan_for(versions, date(2026, 10, 4)).habit("read") is not None     # last week still scored on the old plan
    assert plan_for(versions, date(2026, 10, 5)).habits == ()
    assert plan_for(versions, date(2026, 9, 27)) is None                        # before any plan existed
    assert (root / "plan" / "history" / "2026-09-28.yaml").exists()


def test_a_new_version_must_start_later_than_the_current_one(tmp_path):
    root = make_repo(tmp_path)
    with pytest.raises(ConfigError, match="already effective"):
        write_version(root, parse_plan({"effective_from": "2026-09-28", "habits": []}, "plan"))


def test_a_bedtime_after_midnight_is_planned_on_the_next_calendar_day(tmp_path):
    profile = load_profile(make_repo(tmp_path))
    plan = parse_plan({"effective_from": "2026-09-28", "habits": [
        {"id": "sleep", "name": "Sleep", "days": "daily", "start": "00:30", "duration_min": 480},
        {"id": "dinner", "name": "Dinner", "days": "daily", "start": "03:59", "duration_min": 30},
        {"id": "wake", "name": "Wake", "days": "daily", "start": "04:00", "duration_min": 5},
    ]}, "plan")
    assert planned_start(plan.habit("sleep"), date(2026, 9, 28), profile) == ts("2026-09-29", "00:30")
    assert planned_start(plan.habit("dinner"), date(2026, 9, 28), profile) == ts("2026-09-29", "03:59")
    assert planned_start(plan.habit("wake"), date(2026, 9, 28), profile) == ts("2026-09-28", "04:00")


def test_warnings_flag_a_plan_nobody_can_keep(tmp_path):
    raw = {"effective_from": "2026-09-28", "habits": [
        {"id": f"h{i}", "name": f"H{i}", "days": "daily", "start": "07:00", "duration_min": 5} for i in range(6)]}
    keys = [key for key, _ in check_warnings(parse_plan(raw, "plan"))]
    assert keys.count("plan.warn.too_many") == 1 and keys.count("plan.warn.no_anchor") == 6 and keys.count("plan.warn.no_tiny") == 6


# ---------- logs ----------

def test_voided_and_duplicated_lines_collapse(tmp_path):
    root = make_repo(tmp_path)
    profile = load_profile(root)
    keep = log(root, "2026-09-28", "07:50", "read")
    gone = log(root, "2026-09-28", "07:55", None, kind="note", text="oops")
    logs.append_void(root, profile, date(2026, 9, 28), gone.id, when("2026-09-28", "08:00"))
    path = logs.day_path(root, date(2026, 9, 28))
    path.write_text(path.read_text(encoding="utf-8") * 2, encoding="utf-8")       # what a union merge can produce
    assert [e.id for e in logs.read_day(root, profile, date(2026, 9, 28))] == [keep.id]


def test_an_event_belongs_to_the_logical_day_not_the_calendar_day(tmp_path):
    root = make_repo(tmp_path)
    profile = load_profile(root)
    log(root, "2026-09-29", "00:45", "read", kind="note")
    assert logical_date(ts("2026-09-29", "00:45"), profile) == date(2026, 9, 28)
    assert logs.read_day(root, profile, date(2026, 9, 28))
    assert not logs.read_day(root, profile, date(2026, 9, 29))


def test_a_corrupt_line_names_the_file_and_line(tmp_path):
    root = make_repo(tmp_path)
    log(root, "2026-09-28", "07:50", "read")
    path = logs.day_path(root, date(2026, 9, 28))
    path.write_text(path.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=r"2026-09-28\.jsonl:2"):
        logs.read_day(root, load_profile(root), date(2026, 9, 28))


# ---------- scoring ----------

def habit_read(root):
    return load_versions(root)[0].habit("read")


@pytest.mark.parametrize("actual, status, score", [
    ("07:45", "on_time", 1.0),
    ("08:00", "on_time", 1.0),       # exactly the grace limit
    ("08:01", "late", 0.7),
    ("09:45", "late", 0.7),          # exactly the late limit
    ("09:46", "off_window", 0.4),
    ("06:15", "on_time", 1.0),       # exactly the early limit
    ("06:14", "off_window", 0.4),
    ("21:00", "off_window", 0.4),
])
def test_timing_windows_have_exact_boundaries(tmp_path, actual, status, score):
    root = make_repo(tmp_path)
    profile = load_profile(root)
    log(root, "2026-09-28", actual, "read")
    result = resolve(habit_read(root), date(2026, 9, 28), logs.read_day(root, profile, date(2026, 9, 28)), ts("2026-10-05", "08:00"), profile)
    assert (result.status, result.score) == (status, pytest.approx(score))


def test_the_tiny_version_counts_but_scores_less(tmp_path):
    root = make_repo(tmp_path)
    profile = load_profile(root)
    log(root, "2026-09-28", "07:50", "read", tiny=True)
    result = resolve(habit_read(root), date(2026, 9, 28), logs.read_day(root, profile, date(2026, 9, 28)), ts("2026-10-05", "08:00"), profile)
    assert result.done and result.tiny and result.score == pytest.approx(TINY_FACTOR)
    log(root, "2026-09-28", "08:30", "read", kind="done")              # a later full completion does not erase the first
    result = resolve(habit_read(root), date(2026, 9, 28), logs.read_day(root, profile, date(2026, 9, 28)), ts("2026-10-05", "08:00"), profile)
    assert not result.tiny


def test_the_earliest_start_decides_the_timing(tmp_path):
    root = make_repo(tmp_path)
    profile = load_profile(root)
    log(root, "2026-09-28", "09:00", "read", kind="done")
    log(root, "2026-09-28", "07:50", "read", kind="start")
    result = resolve(habit_read(root), date(2026, 9, 28), logs.read_day(root, profile, date(2026, 9, 28)), ts("2026-10-05", "08:00"), profile)
    assert result.status == "on_time" and result.offset_min == 5


def test_a_skip_is_a_lapse_unless_it_is_excused(tmp_path):
    root = make_repo(tmp_path)
    profile = load_profile(root)
    log(root, "2026-09-28", "07:00", "read", kind="skip", text="slept badly")
    log(root, "2026-09-29", "07:00", "read", kind="skip", text="fever", excused=True)
    now = ts("2026-10-05", "08:00")
    monday = resolve(habit_read(root), date(2026, 9, 28), logs.read_day(root, profile, date(2026, 9, 28)), now, profile)
    tuesday = resolve(habit_read(root), date(2026, 9, 29), logs.read_day(root, profile, date(2026, 9, 29)), now, profile)
    assert (monday.status, monday.counted, monday.reason) == ("skipped", True, "slept badly")
    assert (tuesday.status, tuesday.counted) == ("excused", False)


def test_nothing_logged_is_pending_until_the_day_is_over(tmp_path):
    root = make_repo(tmp_path)
    profile = load_profile(root)
    day = date(2026, 9, 28)
    assert resolve(habit_read(root), day, [], ts("2026-09-29", "03:59"), profile).status == "pending"
    assert resolve(habit_read(root), day, [], ts("2026-09-29", "04:00"), profile).status == "missed"


def test_future_days_and_days_before_the_plan_have_no_occurrences(tmp_path):
    root = make_repo(tmp_path)
    tracker = Tracker(root)
    now = ts("2026-09-30", "12:00")
    assert tracker.day(date(2026, 10, 1), now) == []
    assert tracker.day(date(2026, 9, 21), now) == []


def test_the_weekly_percentage_is_weighted_and_ignores_anchors_pending_and_excused(tmp_path):
    root = make_repo(tmp_path)
    profile = load_profile(root)
    log(root, "2026-09-28", "07:45", "read")          # weight 2, on time
    log(root, "2026-09-28", "07:40", "train")         # anchor: tracked, not scored
    now = ts("2026-09-29", "05:00")                   # Monday is over: walk is missed
    results = score_day(load_versions(root)[0], date(2026, 9, 28), logs.read_day(root, profile, date(2026, 9, 28)), now, profile)
    summary = summarize(results)
    assert summary.percent == pytest.approx(100 * (2 * 1.0 + 1 * 0.0) / 3)
    assert (summary.counted, summary.done) == (2, 1)
    midday = score_day(load_versions(root)[0], date(2026, 9, 28), logs.read_day(root, profile, date(2026, 9, 28)), ts("2026-09-28", "12:00"), profile)
    assert summarize(midday).percent == pytest.approx(100.0) and summarize(midday).pending == 1   # walk is not yet a miss


def test_percent_is_none_when_nothing_can_be_counted(tmp_path):
    root = make_repo(tmp_path)
    tracker = Tracker(root)
    assert tracker.week(date(2026, 9, 28), ts("2026-09-28", "06:00")).summary.percent is None


def test_week_stats_expose_the_median_offset_and_the_worst_weekday(tmp_path):
    root = make_repo(tmp_path)
    for day, at in (("2026-09-28", "08:20"), ("2026-09-29", "08:25"), ("2026-09-30", "08:30")):
        log(root, day, at, "read")
    week = Tracker(root).week(date(2026, 9, 28), ts("2026-10-05", "08:00"))
    stats = week.stats["read"]
    assert (stats.planned, stats.done, stats.median_offset) == (5, 3, 40)
    assert stats.misses_by_weekday == {3: 1, 4: 1}


def test_streak_skips_excused_days_stops_at_a_miss_and_ignores_pending_today(tmp_path):
    root = make_repo(tmp_path)
    log(root, "2026-09-28", "07:45", "read")
    log(root, "2026-09-29", "07:45", "read")
    log(root, "2026-09-30", "07:00", "read", kind="skip", excused=True, text="sick")
    log(root, "2026-10-01", "07:45", "read")
    tracker = Tracker(root)
    assert tracker.streak("read", ts("2026-10-02", "06:00")) == 3      # Fri still pending, Wed excused
    assert Tracker(root).streak("read", ts("2026-10-03", "12:00")) == 0  # Fri was missed and is over
