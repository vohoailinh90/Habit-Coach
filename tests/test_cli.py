from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from habit_coach import logs
from habit_coach.cli import main
from habit_coach.config import load_profile
from habit_coach.plan import load_versions
from tests.support.repo import log, make_repo, ts

ROOT = Path(__file__).resolve().parents[1]


def run(root, *argv, now="2026-09-28T07:50", env=None):
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), root=root, env={"HABIT_NOW": now, **(env or {})}, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def events(root, day):
    return logs.read_day(root, load_profile(root), date.fromisoformat(day))


def test_logging_a_message_writes_one_line_and_reports_progress(tmp_path):
    root = make_repo(tmp_path)
    code, out, _ = run(root, "log", "--habit", "read", "sat down on the train")
    assert code == 0 and "Logged" in out and "on time" in out and "Today's progress" in out
    (event,) = events(root, "2026-09-28")
    assert (event.habit, event.kind, event.text) == ("read", "start", "sat down on the train")
    assert event.ts == ts("2026-09-28", "07:50") and event.logged_at is not None


def test_today_json_is_what_claude_reads_before_it_answers(tmp_path):
    root = make_repo(tmp_path)
    run(root, "log", "--habit", "read")
    code, out, _ = run(root, "today", "--json", now="2026-09-28T09:00")
    data = json.loads(out)
    read = next(o for o in data["occurrences"] if o["habit"] == "read")
    assert code == 0 and read["status"] == "on_time" and read["offset_min"] == 5 and data["date"] == "2026-09-28"
    assert next(o for o in data["occurrences"] if o["habit"] == "walk")["status"] == "pending"
    assert len(data["events"]) == 1


def test_a_backfilled_entry_lands_on_its_own_day_and_remembers_when_it_was_written(tmp_path):
    root = make_repo(tmp_path)
    code, _, _ = run(root, "log", "--habit", "walk", "--at", "19:40", "--date", "2026-09-28", now="2026-09-29T09:00")
    (event,) = events(root, "2026-09-28")
    assert code == 0 and event.ts == ts("2026-09-28", "19:40") and event.logged_at == ts("2026-09-29", "09:00")


def test_a_late_night_entry_belongs_to_the_evening_before(tmp_path):
    root = make_repo(tmp_path)
    code, _, _ = run(root, "log", "went to bed", now="2026-09-29T00:40")
    assert code == 0 and len(events(root, "2026-09-28")) == 1 and not events(root, "2026-09-29")
    run(root, "log", "--at", "00:15", "--date", "2026-09-28", "scrolling", now="2026-09-29T08:00")
    assert ts("2026-09-29", "00:15") in [e.ts for e in events(root, "2026-09-28")]       # 00:15 means the small hours after the 28th


def test_mistakes_are_refused_with_a_message_and_a_nonzero_exit(tmp_path):
    root = make_repo(tmp_path)
    cases = [
        (("log", "--habit", "nope"), "Unknown habit 'nope'"),
        (("log", "--habit", "read", "--at", "09:00"), "in the future"),
        (("log", "--habit", "read", "--at", "25:00"), "not a time"),
        (("log", "--date", "2026-09-28", "x"), "--date needs --at"),
        (("log", "--kind", "done"), "--habit is required"),
        (("log", "--kind", "skip", "--habit", "read", "--tiny"), "--tiny only applies"),
        (("log",), "needs some text"),
        (("report", "--week", "2026W41"), "not an ISO week"),
        (("void", "deadbeef"), "No event"),
    ]
    for argv, fragment in cases:
        code, out, err = run(root, "--lang", "en", *argv)
        assert code == 2 and fragment in err and not out, (argv, code, err)
    assert not events(root, "2026-09-28")


def test_void_removes_an_entry_from_the_score_without_editing_history(tmp_path):
    root = make_repo(tmp_path)
    run(root, "log", "--habit", "read")
    (event,) = events(root, "2026-09-28")
    code, out, _ = run(root, "void", event.id)
    assert code == 0 and not events(root, "2026-09-28")
    assert len(logs.day_path(root, date(2026, 9, 28)).read_text(encoding="utf-8").splitlines()) == 2   # original + tombstone


def test_the_report_numbers_match_the_log(tmp_path):
    root = make_repo(tmp_path)
    for day in ("2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"):
        log(root, day, "07:45", "read")
    code, out, _ = run(root, "report", "--week", "2026-W40", "--json", now="2026-10-05T08:00")
    data = json.loads(out)
    assert code == 0 and data["week"] == "2026-W40" and data["habits"]["read"]["done"] == 5
    assert data["percent"] == round(100 * (5 * 2) / (5 * 2 + 3), 1)           # walk never done: 10 of 13 weighted points
    code, text, _ = run(root, "--lang", "en", "report", "--week", "2026-W40", now="2026-10-05T08:00")
    assert "Plan adherence: 77%" in text and "Read: 5/5 done" in text


def test_review_write_and_apply_make_next_weeks_plan_and_keep_the_notes(tmp_path):
    root = make_repo(tmp_path)
    log(root, "2026-09-28", "07:45", "read")
    code, out, _ = run(root, "--lang", "en", "review", "--date", "2026-10-04", "--write", "--apply", now="2026-10-04T20:00")
    assert code == 0 and "Applied 2 change(s)" in out
    review_file = root / "reviews" / "2026-W40.md"
    review_file.write_text(review_file.read_text(encoding="utf-8") + "\nMy own note.\n", encoding="utf-8")
    run(root, "--lang", "en", "review", "--date", "2026-10-04", "--write", now="2026-10-04T20:30")
    assert "My own note." in review_file.read_text(encoding="utf-8")                    # the generated block is rewritten, notes survive
    assert (root / "plan" / "history" / "2026-09-28.yaml").exists()
    code, _, err = run(root, "--lang", "en", "review", "--date", "2026-10-04", "--apply", now="2026-10-04T21:00")
    assert code == 2 and "not written" in err                                           # applying twice is refused, not repeated


def test_ics_export_writes_a_file_and_json_goes_to_stdout(tmp_path):
    root = make_repo(tmp_path)
    code, out, _ = run(root, "ics", "--from", "2026-09-28", "--weeks", "2", "--out", str(tmp_path / "x.ics"))
    text = (tmp_path / "x.ics").read_bytes().decode("utf-8")
    assert code == 0 and text.count("BEGIN:VEVENT") == 26 and "\r\n" in text            # (5 read + 3 walk + 5 train) x 2 weeks
    code, out, _ = run(root, "ics", "--from", "2026-09-28", "--weeks", "1", "--format", "json")
    assert code == 0 and len(json.loads(out)) == 13


def test_plan_check_and_new_version(tmp_path):
    root = make_repo(tmp_path)
    code, out, _ = run(root, "--lang", "en", "plan", "check")
    assert code == 0 and "Plan OK: 3 habit(s)" in out
    code, out, _ = run(root, "--lang", "en", "plan", "new-version", "--from", "2026-10-05")
    assert code == 0 and "effective 2026-10-05" in out and (root / "plan" / "history" / "2026-09-28.yaml").exists()
    code, _, err = run(root, "--lang", "en", "plan", "new-version", "--from", "2026-10-05")
    assert code == 2


def test_a_broken_plan_is_reported_not_swallowed(tmp_path):
    root = make_repo(tmp_path, plan="effective_from: 2026-09-28\nhabits:\n  - {id: a, name: A, days: daily, start: noon, duration_min: 5}\n")
    code, _, err = run(root, "today")
    assert code == 1 and "start must be" in err


def test_language_follows_the_flag_then_the_environment_then_the_profile(tmp_path):
    root = make_repo(tmp_path)                                         # the profile says en
    assert "Monday" in run(root, "today")[1]
    assert "月曜日" in run(root, "--lang", "ja", "today")[1]
    assert "月曜日" in run(root, "today", env={"APP_LANG": "ja"})[1]
    assert "Thứ Hai" in run(root, "--lang", "vi", "today", env={"APP_LANG": "ja"})[1]


def test_no_plan_yet_is_an_invitation_not_an_error(tmp_path):
    root = make_repo(tmp_path, plan="effective_from: 2026-09-28\nhabits: []\n")
    code, out, _ = run(root, "--lang", "en", "today")
    assert code == 0 and "No plan yet" in out


def test_the_launcher_runs_as_a_script():
    done = subprocess.run([sys.executable, str(ROOT / "habit.py"), "--version"], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0 and "habit-coach" in done.stdout


def test_a_pending_habit_says_how_late_it_is(tmp_path):
    root = make_repo(tmp_path)
    expectations = {"2026-09-28T05:00": "coming up", "2026-09-28T07:50": "time to start", "2026-09-28T08:30": "still doable",
                    "2026-09-28T11:00": "at a lower score"}
    for now, fragment in expectations.items():
        out = run(root, "--lang", "en", "today", now=now)[1]
        assert fragment in next(line for line in out.splitlines() if "Read" in line), (now, out)


def test_a_small_hours_time_said_in_the_morning_means_the_night_just_gone(tmp_path):
    root = make_repo(tmp_path)
    assert run(root, "log", "--at", "00:30", "bedtime", now="2026-09-29T10:00")[0] == 0     # "I went to bed at 00:30"
    assert run(root, "log", "--at", "00:45", "scrolling", now="2026-09-29T00:50")[0] == 0   # said just after it happened
    assert [e.ts for e in events(root, "2026-09-28")] == [ts("2026-09-29", "00:30"), ts("2026-09-29", "00:45")]
    assert not events(root, "2026-09-29")
    # a small-hours time later than "now" on the calendar day can only be yesterday's
    assert run(root, "log", "--at", "03:30", "woke up", now="2026-09-29T01:00")[0] == 0
    assert [e.ts for e in events(root, "2026-09-27")] == [ts("2026-09-28", "03:30")]


def test_an_evening_time_said_at_noon_is_refused_not_guessed(tmp_path):
    root = make_repo(tmp_path)
    code, _, err = run(root, "--lang", "en", "log", "--at", "19:40", "walked", now="2026-09-29T12:00")
    assert code == 2 and "in the future" in err and "--date" in err                          # the hint says how to log yesterday


def test_nonsense_input_is_a_message_not_a_traceback(tmp_path):
    root = make_repo(tmp_path)
    for argv in (("report", "--date", "0001-01-01"), ("report", "--week", "0001-W01"), ("review", "--date", "9999-12-31"),
                 ("ics", "--weeks", "0"), ("ics", "--weeks", "-1"), ("ics", "--weeks", "60")):
        code, out, err = run(root, "--lang", "en", *argv)
        assert code == 2 and err.startswith("error:") and "Traceback" not in err and not out, (argv, err)


def test_files_that_are_not_utf8_are_reported_cleanly(tmp_path):
    root = make_repo(tmp_path)
    path = logs.day_path(root, date(2026, 9, 28))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xfe")
    code, _, err = run(root, "report", "--week", "2026-W40", now="2026-10-05T08:00")
    assert code == 1 and "not valid UTF-8" in err and "Traceback" not in err
    (root / "plan" / "current.yaml").write_bytes(b"\xff\xfe")
    code, _, err = run(root, "today")
    assert code == 1 and "current.yaml" in err


def test_review_with_no_week_reviews_the_week_that_is_owed(tmp_path):
    root = make_repo(tmp_path)
    log(root, "2026-09-28", "07:45", "read")
    monday = json.loads(run(root, "review", "--json", now="2026-10-05T09:00")[1])           # Monday morning: last week's review is owed
    sunday = json.loads(run(root, "review", "--json", now="2026-10-04T20:00")[1])           # Sunday evening: this week's
    wednesday = json.loads(run(root, "review", "--json", now="2026-10-07T20:00")[1])       # still within the three days of grace
    thursday = json.loads(run(root, "review", "--json", now="2026-10-08T20:00")[1])        # nothing owed any more: the current week
    assert (monday["week"], sunday["week"], wednesday["week"], thursday["week"]) == ("2026-W40", "2026-W40", "2026-W40", "2026-W41")


def test_moments_that_assert_something_cannot_be_forced(tmp_path):
    root = make_repo(tmp_path)
    for moment in ("comeback", "pre", "milestone", "done"):
        with pytest.raises(SystemExit) as stopped:
            run(root, "nudge", "--moment", moment)
        assert stopped.value.code == 2
    assert run(root, "nudge", "--moment", "evening")[0] == 0


def test_a_plan_version_clash_is_explained_in_the_owners_language(tmp_path):
    root = make_repo(tmp_path)
    code, _, err = run(root, "--lang", "vi", "plan", "new-version", "--from", "2026-09-28")
    assert code == 2 and "kế hoạch hiện tại" in err and "2026-09-28" in err and "the current plan" not in err


STRETCH = "effective_from: 2026-09-28\nhabits:\n  - {id: stretch, name: Stretch, days: weekdays, start: '07:00', duration_min: 15, anchor: after coffee, tiny: one pose}\n"


@pytest.mark.parametrize("lang, friday", [("en", "Friday"), ("vi", "Thứ Sáu"), ("ja", "金曜日")])
def test_a_review_that_drops_a_day_renders_in_every_language_and_applies(tmp_path, lang, friday):
    root = make_repo(tmp_path, plan=STRETCH)                              # never done, already 15 minutes: the worst day goes
    code, out, err = run(root, "--lang", lang, "review", "--date", "2026-10-04", now="2026-10-04T21:00")
    assert code == 0 and friday in out and "Traceback" not in err, err
    data = json.loads(run(root, "review", "--date", "2026-10-04", "--json", now="2026-10-04T21:00")[1])
    assert [(p["kind"], p["old"]) for p in data["proposals"]] == [("drop_day", 4)]
    assert run(root, "review", "--date", "2026-10-04", "--write", "--apply", now="2026-10-04T21:00")[0] == 0
    assert load_versions(root)[-1].habit("stretch").days == (0, 1, 2, 3)


def test_a_review_is_owed_until_its_narrative_is_written_and_for_three_days(tmp_path):
    root = make_repo(tmp_path)
    log(root, "2026-09-28", "07:45", "read")
    run(root, "review", "--date", "2026-10-04", "--write", now="2026-10-04T20:00")       # the run dies here, before any narrative
    review = root / "reviews" / "2026-W40.md"
    assert "narrative: pending" in review.read_text(encoding="utf-8")
    for now in ("2026-10-04T21:00", "2026-10-05T09:00", "2026-10-06T09:00", "2026-10-07T09:00"):
        assert "2026-W40" in run(root, "--lang", "en", "today", now=now)[1], now
    assert "2026-W40" not in run(root, "--lang", "en", "today", now="2026-10-08T09:00")[1]    # grace is over
    run(root, "review", "--date", "2026-10-04", "--write", now="2026-10-05T09:00")           # re-running keeps it pending
    assert "narrative: pending" in review.read_text(encoding="utf-8")
    written = review.read_text(encoding="utf-8").replace(
        next(line for line in review.read_text(encoding="utf-8").splitlines() if "narrative: pending" in line) + "\n", "")
    review.write_text(written, encoding="utf-8")
    assert "2026-W40" not in run(root, "--lang", "en", "today", now="2026-10-05T09:00")[1]   # narrative in place: done


def test_the_same_words_in_the_same_minute_are_one_entry(tmp_path):
    root = make_repo(tmp_path)
    first = run(root, "--lang", "en", "log", "--habit", "read", "sat down")
    second = run(root, "--lang", "en", "log", "--habit", "read", "sat down")
    assert "Logged" in first[1] and "Already logged" in second[1] and second[0] == 0
    raw = logs.day_path(root, date(2026, 9, 28))
    assert len(raw.read_text(encoding="utf-8").splitlines()) == 1             # on disk too: read_day alone would hide a second line
    third = run(root, "--lang", "en", "log", "--habit", "read", "sat down again")           # different words: a different entry
    assert "Logged" in third[1] and len(raw.read_text(encoding="utf-8").splitlines()) == 2


def test_help_of_nested_subcommands_is_localized_too(tmp_path, capsys):
    root = make_repo(tmp_path)
    with pytest.raises(SystemExit) as stopped:
        run(root, "--lang", "vi", "plan", "new-version", "--help")
    shown = capsys.readouterr().out
    assert stopped.value.code == 0 and "Ngày đầu tiên" in shown and "Hiện trợ giúp" in shown and "show this help" not in shown
