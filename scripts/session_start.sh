#!/bin/sh
# SessionStart hook: show today's plan, progress and a nudge so the session starts oriented.
cd "$(dirname "$0")/.." || exit 0
python3 -c "import yaml, zoneinfo" 2>/dev/null || pip install -q -r requirements.txt >/dev/null 2>&1
python3 habit.py today || true
