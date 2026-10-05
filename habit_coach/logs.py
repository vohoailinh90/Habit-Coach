"""The activity log: what actually happened, one JSON line per message.

`logs/YYYY/YYYY-MM-DD.jsonl`, named by *logical* day (see config.logical_date).
Append-only: a mistake is corrected with a `void` tombstone, never by editing a
line, so the file's git history is a faithful diary and `merge=union` in
.gitattributes can merge two branches that both appended to the same day.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import ConfigError, Profile, logical_date

KINDS = ("start", "done", "skip", "note")
COMPLETION_KINDS = ("start", "done")


@dataclass(frozen=True)
class Event:
    id: str
    ts: datetime            # when it happened, in the owner's timezone
    kind: str               # start | done | skip | note
    habit: str | None = None
    text: str = ""          # the owner's own words; for a skip, the reason
    tiny: bool = False      # the two-minute version, which counts but scores less
    excused: bool = False   # a skip that is rest, illness or travel, not a lapse
    logged_at: datetime | None = None  # when it was written down; ts - logged_at shows backfilled entries


def event_id(ts: datetime, kind: str, habit: str | None, text: str, logged_at: datetime) -> str:
    raw = "|".join((ts.isoformat(), kind, habit or "", text, logged_at.isoformat()))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def day_path(root: Path, day: date) -> Path:
    return root / "logs" / f"{day.year:04d}" / f"{day.isoformat()}.jsonl"


def _encode(event: Event) -> dict:
    data: dict = {"id": event.id, "ts": event.ts.isoformat(), "kind": event.kind}
    if event.habit:
        data["habit"] = event.habit
    if event.text:
        data["text"] = event.text
    if event.tiny:
        data["tiny"] = True
    if event.excused:
        data["excused"] = True
    if event.logged_at:
        data["logged_at"] = event.logged_at.isoformat()
    return data


def _decode(data: dict, where: str, profile: Profile) -> Event:
    try:
        ts = datetime.fromisoformat(data["ts"])
        logged = datetime.fromisoformat(data["logged_at"]) if data.get("logged_at") else None
        return Event(
            id=str(data["id"]),
            ts=ts.replace(tzinfo=profile.tz) if ts.tzinfo is None else ts.astimezone(profile.tz),
            kind=str(data["kind"]),
            habit=data.get("habit"),
            text=str(data.get("text", "")),
            tiny=bool(data.get("tiny", False)),
            excused=bool(data.get("excused", False)),
            logged_at=logged,
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise ConfigError(f"{where}: unreadable log entry ({exc!r})") from exc


def append_event(root: Path, profile: Profile, event: Event) -> Path:
    path = day_path(root, logical_date(event.ts, profile))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(_encode(event), ensure_ascii=False) + "\n")
    return path


def append_void(root: Path, profile: Profile, day: date, ref: str, logged_at: datetime) -> Path:
    path = day_path(root, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = {"kind": "void", "ref": ref, "logged_at": logged_at.isoformat()}
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    return path


def read_day(root: Path, profile: Profile, day: date) -> list[Event]:
    """The day's live events in time order, with voided ones removed."""
    path = day_path(root, day)
    if not path.exists():
        return []
    events: list[Event] = []
    voided: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{path.relative_to(root)}: not valid UTF-8 ({exc.reason})") from exc
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        where = f"{path.relative_to(root)}:{number}"
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{where}: not valid JSON ({exc.msg})") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{where}: expected a JSON object")
        if data.get("kind") == "void":
            voided.add(str(data.get("ref")))
        else:
            events.append(_decode(data, where, profile))
    live = {e.id: e for e in events if e.id not in voided}   # union-merged duplicates collapse here
    return sorted(live.values(), key=lambda e: e.ts)


def find_event(root: Path, profile: Profile, ref: str, around: date, days_back: int = 14) -> tuple[date, Event] | None:
    for offset in range(days_back + 1):
        day = around - timedelta(days=offset)
        for event in read_day(root, profile, day):
            if event.id == ref:
                return day, event
    return None

