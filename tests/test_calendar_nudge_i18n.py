from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from habit_coach.calendar_export import build_events, events_as_json, fold, render_ics
from habit_coach.config import load_profile
from habit_coach.i18n import Translator, flatten
from habit_coach.nudge import KINDS, MILESTONES, VARIANTS, pick_nudge
from habit_coach.plan import load_versions, parse_plan, write_version
from habit_coach.tracker import Tracker
from tests.support.repo import PROFILE, log, make_repo, ts

PACKAGE = Path(__file__).resolve().parents[1] / "habit_coach"
ROOT = PACKAGE.parent
LOCALES = PACKAGE / "locales"


def translator(locale="en"):
    return Translator.from_dir(LOCALES, locale)


def unfold(text: str) -> list[str]:
    return text.replace("\r\n ", "").split("\r\n")


# ---------- calendar ----------

def test_events_use_the_plan_version_in_force_on_each_day(tmp_path):
    root = make_repo(tmp_path)
    write_version(root, parse_plan({"effective_from": "2026-10-05", "habits": [
        {"id": "read", "name": "Read", "days": "weekdays", "start": "07:00", "duration_min": 10}]}, "plan"))
    events = build_events(load_versions(root), date(2026, 10, 2), date(2026, 10, 6), load_profile(root), translator())
    mine = {e.uid: e for e in events if e.habit_id == "read"}
    assert mine["read-20261002@habit-coach"].start == ts("2026-10-02", "07:45")      # Friday: old plan
    assert mine["read-20261005@habit-coach"].start == ts("2026-10-05", "07:00")      # Monday: new plan
    assert not [e for e in events if e.habit_id == "walk" and e.start.date() >= date(2026, 10, 5)]


def test_ics_is_valid_enough_for_google_calendar(tmp_path):
    root = make_repo(tmp_path)
    profile = load_profile(root)
    events = build_events(load_versions(root), date(2026, 9, 28), date(2026, 9, 28), profile, translator("vi"))
    text = render_ics(events, "Habit Coach, Việc; hằng ngày", ts("2026-09-27", "20:00"))
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    raw_lines = text.split("\r\n")[:-1]
    assert all(len(line.encode("utf-8")) <= 75 for line in raw_lines)               # folded, never mid-character
    lines = unfold(text)
    assert lines[0] == "BEGIN:VCALENDAR" and lines[-2] == "END:VCALENDAR"
    assert lines.count("BEGIN:VEVENT") == lines.count("END:VEVENT") == 3
    assert "X-WR-CALNAME:Habit Coach\\, Việc\\; hằng ngày" in lines
    assert "UID:read-20260928@habit-coach" in lines
    assert "DTSTART:20260927T224500Z" in lines                                        # 07:45 JST is 22:45 UTC the day before
    assert "TRIGGER:-PT10M" in lines
    description = next(line for line in lines if line.startswith("DESCRIPTION:Mốc"))
    assert "\\n" in description and "one page" in description


def test_folding_never_splits_a_multibyte_character():
    folded = fold("SUMMARY:" + "ệ" * 60)
    assert all(len(part.encode("utf-8")) <= 75 for part in folded)
    assert "".join(part[1:] if i else part for i, part in enumerate(folded)) == "SUMMARY:" + "ệ" * 60


def test_the_json_export_carries_what_the_connector_needs(tmp_path):
    root = make_repo(tmp_path)
    profile = load_profile(root)
    events = build_events(load_versions(root), date(2026, 9, 28), date(2026, 9, 28), profile, translator())
    rows = events_as_json(events, profile)
    read = next(r for r in rows if r["habit"] == "read")
    assert read["start"] == "2026-09-28T07:45:00+09:00" and read["end"] == "2026-09-28T08:15:00+09:00"
    assert read["timezone"] == "Asia/Tokyo" and read["reminder_min"] == 10 and "one page" in read["description"]


def test_a_bedtime_after_midnight_lands_on_the_next_calendar_day_in_the_calendar(tmp_path):
    root = make_repo(tmp_path, plan="effective_from: 2026-09-28\nhabits:\n  - {id: sleep, name: Sleep, days: daily, start: '00:30', duration_min: 60}\n")
    event = build_events(load_versions(root), date(2026, 9, 28), date(2026, 9, 28), load_profile(root), translator())[0]
    assert event.start == ts("2026-09-29", "00:30")


# ---------- nudge ----------

def nudge_at(root, moment, kind=None):
    return pick_nudge(Tracker(root), moment, translator(), kind)


def test_a_missed_habit_gets_a_never_miss_twice_nudge_the_next_day(tmp_path):
    root = make_repo(tmp_path)                                     # Monday read missed, Tuesday read still pending
    assert nudge_at(root, ts("2026-09-29", "07:00")).kind == "comeback"


def test_an_upcoming_habit_gets_a_pre_nudge_with_its_cue(tmp_path):
    root = make_repo(tmp_path)
    log(root, "2026-09-28", "07:45", "read")
    log(root, "2026-09-28", "19:30", "walk")                       # nothing missed yesterday for Tuesday
    nudge = nudge_at(root, ts("2026-09-29", "07:20"))
    assert nudge.kind == "pre" and nudge.habit_id == "read" and "sit down on the train" in nudge.text


def test_a_streak_milestone_is_celebrated(tmp_path):
    root = make_repo(tmp_path)
    for day in ("2026-09-28", "2026-09-29", "2026-09-30"):
        log(root, day, "07:45", "read")
    log(root, "2026-09-28", "19:30", "walk")
    nudge = nudge_at(root, ts("2026-09-30", "13:00"))
    assert 3 in MILESTONES and nudge.kind == "milestone" and "3" in nudge.text


def test_a_just_logged_habit_gets_a_reinforcement(tmp_path):
    root = make_repo(tmp_path)
    log(root, "2026-09-28", "07:50", "read")
    assert nudge_at(root, ts("2026-09-28", "08:05")).kind == "done"


def test_evening_morning_and_generic_fall_back_by_the_hour(tmp_path):
    root = make_repo(tmp_path)
    for day in ("2026-09-28",):
        log(root, day, "07:45", "read")
        log(root, day, "19:30", "walk")
    assert nudge_at(root, ts("2026-09-28", "21:00")).kind == "evening"
    assert nudge_at(root, ts("2026-09-28", "06:00")).kind == "morning"
    assert nudge_at(root, ts("2026-09-28", "15:00")).kind == "generic"


def test_the_same_state_gives_the_same_sentence_and_every_kind_can_be_forced(tmp_path):
    root = make_repo(tmp_path)
    moment = ts("2026-09-29", "07:20")
    assert nudge_at(root, moment).text == nudge_at(root, moment).text
    for kind in KINDS:
        assert nudge_at(root, moment, kind).text.strip()
    with pytest.raises(ValueError):
        nudge_at(root, moment, "bogus")


def test_the_owners_own_words_replace_the_defaults(tmp_path):
    root = make_repo(tmp_path, profile=PROFILE + 'identity: "I finish what I start"\nwhy: "my daughter"\n')
    texts = [nudge_at(root, ts(f"2026-10-{day:02d}", "15:00"), "generic").text for day in range(5, 25)]
    assert any("my daughter" in t for t in texts) and any("I finish what I start" in t for t in texts)
    assert not any("keep the promises" in t for t in texts)               # the default identity is gone


# ---------- catalogs ----------

CODE_KEY = re.compile(r"""\bt\(\s*(?:f)?["']([a-z_.0-9{}A-Z]+)["']""")


def catalog(locale):
    return flatten(json.loads((LOCALES / f"{locale}.json").read_text(encoding="utf-8")))


def test_every_literal_key_in_the_code_exists_in_every_catalog():
    literal = set()
    for source in PACKAGE.glob("*.py"):
        if source.name == "i18n.py":        # the runtime's docstring examples are not this app's keys
            continue
        literal |= {k for k in CODE_KEY.findall(source.read_text(encoding="utf-8")) if "{" not in k}
    assert literal, "the scan found nothing; the pattern is broken"
    for locale in ("ja", "en", "vi"):
        missing = sorted(k for k in literal if k not in catalog(locale) and not any(f"{k}_{p}" in catalog(locale) for p in ("one", "other")))
        assert not missing, f"{locale} lacks {missing}"


def test_every_dynamically_built_key_exists_in_every_catalog():
    wanted = [f"weekday.{i}" for i in range(7)]
    wanted += [f"status.{s}" for s in ("on_time", "late", "off_window", "excused", "skipped", "missed", "pending_upcoming", "pending_due", "pending_overdue", "pending_passed")]
    wanted += [f"review.proposal.{k}" for k in ("reduce_duration", "drop_day", "shift_time", "level_up", "flag")]
    wanted += [f"cmd.{c}" for c in ("log", "void", "today", "report", "review", "plan", "ics", "nudge")]
    wanted += [f"nudge.{kind}.{i}" for kind in KINDS for i in range(VARIANTS)]
    for locale in ("ja", "en", "vi"):
        missing = [k for k in wanted if k not in catalog(locale)]
        assert not missing, f"{locale} lacks {missing}"


def test_a_nudge_only_uses_placeholders_the_code_supplies():
    supplied = {"habit", "tiny", "anchor", "streak", "pct", "done", "total", "remaining", "identity", "why"}
    for locale in ("ja", "en", "vi"):
        for key, message in catalog(locale).items():
            if key.startswith("nudge."):
                assert set(re.findall(r"\{(\w+)\}", message)) <= supplied | {"anchor"}, (locale, key)


def test_the_catalog_gate_and_layout_gate_pass():
    for script, extra in (("i18n_check.py", ["--require", "ja,en,vi"]), ("layout_check.py", [])):
        done = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *extra], capture_output=True, text=True, cwd=ROOT)
        assert done.returncode == 0, done.stdout + done.stderr
