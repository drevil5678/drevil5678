import json
import os
import secrets
import sqlite3
from pathlib import Path
from .credentials import new_team_code


DEFAULT_SETTINGS = {"board_size": 19, "komi": 7.5, "rules": "chinese", "threshold": 0.40,
                    "loss_start_ply": 61, "loss_turns": 3, "visits": 128,
                    "allow_pass": True, "rules_confirmed": False}


class Store:
    def __init__(self, runtime):
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        # All application writes are serialized by the app's asyncio lock.
        # TestClient and some ASGI hosts construct the factory on another thread.
        self.db = sqlite3.connect(self.runtime / "tournament.sqlite3", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT NOT NULL)")
        self.db.commit()
        token_file = self.runtime / "admin-token"
        if not token_file.exists():
            fd = os.open(token_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(secrets.token_urlsafe(32))
        self.admin_token = token_file.read_text().strip()
        row = self.db.execute("SELECT data FROM state WHERE id=1").fetchone()
        self.state = json.loads(row[0]) if row else {
            "settings": DEFAULT_SETTINGS.copy(), "teams": [], "games": [], "rounds": [], "events": []}
        self.pending_events = []
        # Preserve old per-game tokens, while adding a stable code to each team.
        used = [t["play_code"] for t in self.state["teams"] if t.get("play_code")]
        for team in self.state["teams"]:
            if not team.get("play_code"):
                team["play_code"] = new_team_code(used)
                used.append(team["play_code"])
        # Migrate older snapshots once, preserving the complete audit trail.
        if self.state["events"] and self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0:
            self.pending_events.extend(self.state["events"])
        self.state["events"] = self.state["events"][-100:]
        self.save()

    def save(self):
        # Append audit records, rather than reserializing an entire event's
        # tournament history every second while 16 game clocks are running.
        with self.db:
            self.db.executemany("INSERT INTO events (data) VALUES (?)",
                                ((json.dumps(e, ensure_ascii=False),) for e in self.pending_events))
            self.db.execute("INSERT OR REPLACE INTO state VALUES (1,?)", (json.dumps(self.state, ensure_ascii=False),))
        self.pending_events.clear()

    def record_event(self, item):
        self.pending_events.append(item)
        self.state["events"].append(item)
        self.state["events"] = self.state["events"][-100:]

    def all_events(self):
        return [json.loads(row[0]) for row in self.db.execute("SELECT data FROM events ORDER BY id")] + self.pending_events.copy()

    def backup(self):
        destination = self.runtime / "backups"
        destination.mkdir(exist_ok=True, mode=0o700)
        import time
        path = destination / f"tournament-{time.time_ns()}.sqlite3"
        target = sqlite3.connect(path)
        with target:
            self.db.backup(target)
        target.close()
        path.chmod(0o600)
        return path.name
