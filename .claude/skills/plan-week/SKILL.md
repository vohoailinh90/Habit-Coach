---
name: plan-week
description: Onboard the owner and build or rebuild the weekly habit plan, then export it to Google Calendar. Use on the first conversation, when the plan is empty, or when the owner asks to make, redo or change their weekly schedule (lập kế hoạch tuần, tạo lịch, đổi lịch).
---

Build the owner's weekly plan: $ARGUMENTS

A plan fails when it is too big, too vague, or built on a day the owner does not really have. Start from the real day and shrink.

## 1. Learn the real day (a few questions at a time, never a form)

Ask, in Vietnamese, in two or three short rounds:

- **Why** this matters, in one sentence (it goes into `profile.yaml` as `why` and comes back in nudges).
- **A normal weekday and a normal weekend day**: wake, commute (the train is a ready-made anchor), work start and end, meals, sleep.
- **What they want to build**, and what they already do reliably (reliable things are anchors).
- **What got in the way** the last time they tried.
- Confirm **timezone** (`config/profile.yaml` says Asia/Tokyo until told otherwise) and **language**. Ask who they want to become, in their own words, for `identity`. Ask what rewards they would genuinely enjoy for `rewards`.

## 2. Draft small

- At most **3 scored habits for week one** (5 is the ceiling). Fewer, easier, earlier beats more.
- Real fixed moments (board train, start work, bedtime) become **anchors**: `scored: false`. They are tracked so the review sees the real day, and they are what scored habits hang on.
- Every scored habit gets an `anchor` ("after I sit down on the train") and a `tiny` version that takes about two minutes and still counts. One habit may be `keystone: true`: the one the rest of the day hangs on.
- Durations short enough to do on a bad day. `weight` 1-3 by importance. Times in quotes: `"07:45"`.
- Show the draft in chat as a small table (time, habit, cue, tiny version) and get an explicit yes before writing anything.

## 3. Write, check, export

1. If a plan with habits already exists, `python3 habit.py plan new-version --from <date>` first (the Monday after next Sunday's review, or today for an urgent change). Otherwise edit `plan/current.yaml` directly, setting `effective_from`.
2. Write the habits (see `examples/plan.example.yaml` for the shape), then `python3 habit.py plan check`. Fix every error; address warnings or tell the owner why you left them.
3. Update `config/profile.yaml` with timezone, `identity`, `why`, `rewards`.
4. `python3 habit.py ics --weeks 2`, then sync per CLAUDE.md ("Google Calendar").
5. Commit and push (CLAUDE.md, "Persisting").

## 4. Close the loop

Tell the owner, in a few lines: what is in the plan, how calendar alarms will remind them, and exactly how to report ("when you board the train, just message me: *lên tàu*"). End with the first tiny step.
