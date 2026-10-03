"""Local SQLite cache for fetched season stats.

One row per season holds the aggregated result as JSON, so re-selecting a year
loads instantly and works offline. 'Refresh' overwrites the cached row.
"""

import json
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "euroleague_ft.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS season_cache (
            year       INTEGER PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            data       TEXT NOT NULL
        )
        """
    )
    return conn


def load(year: int):
    """Return (data, fetched_at) for a cached season, or (None, None)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT data, fetched_at FROM season_cache WHERE year = ?", (year,)
        ).fetchone()
    if row is None:
        return None, None
    return json.loads(row[0]), row[1]


def save(year: int, data: dict):
    """Insert or replace the cached result for a season. Returns fetched_at."""
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO season_cache (year, fetched_at, data) "
            "VALUES (?, ?, ?)",
            (year, fetched_at, json.dumps(data)),
        )
    return fetched_at


def cached_years():
    """Return a set of years that have cached data."""
    with _connect() as conn:
        rows = conn.execute("SELECT year FROM season_cache").fetchall()
    return {r[0] for r in rows}
