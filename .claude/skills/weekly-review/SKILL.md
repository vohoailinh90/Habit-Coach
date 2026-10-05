---
name: weekly-review
description: Sunday review. Score the week, explain it kindly, apply the small plan changes the script proposes, re-export the calendar and send the owner a short summary. Use on Sundays, when habit.py says the review is due, or when the owner asks how their week went (đánh giá tuần, cải tiến lịch).
---

Run the weekly review: $ARGUMENTS

The week is Monday to Sunday. Default to the week containing today; on a Monday morning, review the week just ended (`--date <last Sunday>`).

## 1. Read the data

```bash
python3 habit.py review --date <Sunday> --json      # numbers and proposals, for you
python3 habit.py review --date <Sunday>             # the same, as the owner will read it
```

Skim that week's `logs/` for the owner's own words and skip reasons: they explain the numbers better than the numbers do.

## 2. Decide what to apply

The script's proposals are conservative and capped. Apply them as proposed unless a note in the logs says otherwise. Ask the owner first only when a proposal touches the keystone habit, or when they have just told you something that changes the picture (a new job, a move, an illness). In an unattended run (a scheduled routine), apply and explain afterwards.

- A week below `struggling_pct`: do not add anything. Lead with understanding, name the likely obstacle from the logs, and let the reductions stand.
- A flag means a habit that cannot shrink further: do not drop it yourself; raise it as a question in the summary.

## 3. Apply and record

```bash
python3 habit.py review --date <Sunday> --write --apply
python3 habit.py ics --weeks 2
```

`--write` saves `reviews/<week>.md` with a generated block; **your narrative goes below the block**, replacing the placeholder section *and the `<!-- narrative: pending ... -->` line above it* (a review whose pending line is still there is treated as not done, and `today` keeps reminding for three days), and it survives re-runs. `--apply` writes next week's plan (old version archived) and refuses to run twice. Then sync the calendar per CLAUDE.md.

## 4. Write the narrative (in Vietnamese, at most 25 lines)

Under the generated block in `reviews/<week>.md`:

1. **What went well**, specific (a streak, a habit that moved on-time, an honest log on a bad day).
2. **The one pattern** the data shows (misses cluster on one weekday, a time that never works, the weeks getting steadier), and what the owner said about it.
3. **What changed in the plan and why**, one line per change.
4. **The one focus for next week.**
5. A short, warm closing line that uses the owner's `identity` or `why` and the reward if one unlocked. No lecture.

## 5. Persist and tell the owner

Commit and push. Then reply with at most five lines: the week's percentage and the change from last week, the one win, what you changed, the one focus, the reward if earned. If this run is a scheduled routine, that reply is the notification the owner receives, so make it worth reading.
