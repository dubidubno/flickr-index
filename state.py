"""
Incremental sync state — tracks downloaded photos to avoid re-fetching.
Stored in .state.json (gitignored).
"""

import json
from pathlib import Path

STATE_FILE = Path(".state.json")


def load() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"photos": {}, "albums": {}}


def save(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))

def mark_photo(state: dict, photo_id: str, data: dict) -> None:
    state["photos"][photo_id] = data
