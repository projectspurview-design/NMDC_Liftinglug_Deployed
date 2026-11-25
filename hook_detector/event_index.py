"""Timestamped changes in visual detection state, kept beside the recordings."""
import sqlite3
from contextlib import closing
from pathlib import Path
import threading
import time

class Events:
    def __init__(self, root, retention_days=365):
        self.path = Path(root) / "nmdc-events.sqlite"
        self.retention_days = retention_days
        self._last_prune_at = 0.0
        self._prune_lock = threading.Lock()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS events (camera TEXT, stamp REAL, state TEXT, ready INTEGER)")
            db.execute("CREATE INDEX IF NOT EXISTS camera_time ON events(camera,stamp)")

    def add(self, camera, stamp, state, ready):
        with closing(sqlite3.connect(self.path,timeout=1)) as db, db:
            db.execute("INSERT INTO events VALUES (?,?,?,?)",(camera,stamp,state,int(ready)))
        now = time.time()
        if now - self._last_prune_at >= 3600:
            with self._prune_lock:
                if now - self._last_prune_at >= 3600:
                    self.prune(now - self.retention_days * 86400)
                    self._last_prune_at = now

    def recent(self,camera):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT stamp,state,ready FROM events WHERE camera=? ORDER BY stamp DESC LIMIT 100",
                              (camera,)).fetchall()

    def engagement_counts(self, start: float, end: float) -> dict[str, int]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                """
                WITH ordered AS (
                    SELECT camera, stamp, ready,
                           LAG(ready, 1, 0) OVER (
                               PARTITION BY camera ORDER BY stamp, rowid
                           ) AS previous_ready
                    FROM events
                    WHERE stamp < ?
                )
                SELECT camera, COUNT(*)
                FROM ordered
                WHERE stamp >= ? AND ready = 1 AND previous_ready = 0
                GROUP BY camera
                """,
                (end, start),
            ).fetchall()
        return {camera: count for camera, count in rows}

    def engagement_starts(self, start: float, end: float) -> list[dict[str, object]]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                """
                WITH ordered AS (
                    SELECT camera, stamp, ready,
                           LAG(ready, 1, 0) OVER (
                               PARTITION BY camera ORDER BY stamp, rowid
                           ) AS previous_ready
                    FROM events
                    WHERE stamp < ?
                )
                SELECT camera, stamp
                FROM ordered
                WHERE stamp >= ? AND ready = 1 AND previous_ready = 0
                ORDER BY stamp, camera
                """,
                (end, start),
            ).fetchall()
        return [
            {"camera_id": camera, "timestamp": stamp}
            for camera, stamp in rows
        ]

    def transitions(
        self,
        start: float,
        end: float,
        *,
        camera: str | None = None,
        bay_id: str | None = None,
        bay_ids: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        if bay_id is not None and bay_ids is not None:
            raise ValueError("Use either bay_id or bay_ids, not both.")
        camera_filter = "AND camera = ?" if camera else ""
        selected_bays = bay_ids if bay_ids is not None else ((bay_id,) if bay_id else ())
        bay_filter = (
            "AND (" + " OR ".join("camera GLOB ?" for _ in selected_bays) + ")"
            if selected_bays
            else ""
        )
        parameters: list[object] = [end]
        if camera:
            parameters.append(camera)
        parameters.extend(f"{selected_bay}-Camera-*" for selected_bay in selected_bays)
        parameters.extend((start, limit))
        with closing(sqlite3.connect(self.path)) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute(
                f"""
                WITH ordered AS (
                    SELECT camera, stamp, state, ready,
                           LAG(ready, 1, 0) OVER (
                               PARTITION BY camera ORDER BY stamp, rowid
                           ) AS previous_ready
                    FROM events
                    WHERE stamp < ? {camera_filter} {bay_filter}
                )
                SELECT camera, stamp, state, ready, previous_ready
                FROM ordered
                WHERE stamp >= ?
                ORDER BY stamp DESC, camera
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        transitions = []
        for row in rows:
            if row["ready"] and not row["previous_ready"]:
                event_type = "engagement_started"
            elif row["previous_ready"] and not row["ready"]:
                event_type = "engagement_ended"
            else:
                event_type = "state_changed"
            transitions.append({
                "camera_id": row["camera"],
                "timestamp": row["stamp"],
                "state_key": row["state"],
                "lift_ready": bool(row["ready"]),
                "event_type": event_type,
            })
        return transitions

    def prune(self,cutoff):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM events WHERE stamp < ?",(cutoff,))
