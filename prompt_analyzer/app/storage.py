import os
import sqlite3
import json
from typing import List
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "themes.db")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS themes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            themes_json TEXT NOT NULL
        )
        """
    )
    # per-message analyses
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            ts TEXT NOT NULL,
            message_text TEXT,
            sentiment_json TEXT,
            risk_tags_json TEXT,
            danger_level TEXT,
            themes_json TEXT
        )
        """
    )
    # If the analyses table existed before this code added themes_json, ensure the column exists
    try:
        cur.execute("PRAGMA table_info(analyses)")
        cols = [r[1] for r in cur.fetchall()]
        if "themes_json" not in cols:
            try:
                cur.execute("ALTER TABLE analyses ADD COLUMN themes_json TEXT")
            except Exception:
                # best-effort; ignore if unable to alter (older SQLite versions)
                pass
    except Exception:
        # ignore any pragma errors
        pass

    # per-day aggregated summaries
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            themes_json TEXT,
            avg_sentiment_json TEXT,
            risk_counts_json TEXT,
            danger_summary TEXT,
            summary_text TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()


def save_user_themes(user_id: str, themes: List[str]):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO themes (user_id, date, themes_json) VALUES (?, ?, ?)",
        (user_id, datetime.utcnow().isoformat(), json.dumps(themes)),
    )
    conn.commit()
    conn.close()


def save_analysis(user_id: str, message_text: str, analysis: dict, ts: str = None, themes: List[str] = None):
    """Save a single analysis row. themes is optional and will be stored as JSON.
    Stored fields include ts, user_id, message_text, sentiment_json, risk_tags_json, danger_level, themes_json.
    """
    ts = ts or datetime.utcnow().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO analyses (user_id, ts, message_text, sentiment_json, risk_tags_json, danger_level, themes_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            ts,
            message_text,
            json.dumps(analysis.get("sentiment")),
            json.dumps(analysis.get("risk_tags")),
            analysis.get("danger_level"),
            json.dumps(themes) if themes is not None else None,
        ),
    )
    conn.commit()
    conn.close()


def get_analyses_for_user_date(user_id: str, date_str: str):
    """Return analyses for a user where ts starts with date_str (YYYY-MM-DD)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    like_pattern = f"{date_str}%"
    cur.execute(
        "SELECT ts, message_text, sentiment_json, risk_tags_json, danger_level, themes_json FROM analyses WHERE user_id = ? AND ts LIKE ? ORDER BY id ASC",
        (user_id, like_pattern),
    )
    rows = cur.fetchall()
    conn.close()
    results = []
    for r in rows:
        try:
            sent = json.loads(r[2]) if r[2] else None
        except Exception:
            sent = None
        try:
            tags = json.loads(r[3]) if r[3] else []
        except Exception:
            tags = []
        try:
            themes = json.loads(r[5]) if r[5] else []
        except Exception:
            themes = []
        results.append({
            "ts": r[0],
            "text": r[1],
            "sentiment": sent,
            "risk_tags": tags,
            "danger_level": r[4],
            "themes": themes,
        })
    return results


def get_user_ids_for_date(date_str: str):
    """Return distinct user_ids that have analyses for the given date prefix (YYYY-MM-DD)."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    like_pattern = f"{date_str}%"
    cur.execute("SELECT DISTINCT user_id FROM analyses WHERE ts LIKE ?", (like_pattern,))
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]


def get_analyses_for_user(user_id: str):
    """Return all analyses for a user ordered by id asc."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT ts, message_text, sentiment_json, risk_tags_json, danger_level, themes_json FROM analyses WHERE user_id = ? ORDER BY id ASC",
        (user_id,),
    )
    rows = cur.fetchall()
    conn.close()
    results = []
    for r in rows:
        try:
            sent = json.loads(r[2]) if r[2] else None
        except Exception:
            sent = None
        try:
            tags = json.loads(r[3]) if r[3] else []
        except Exception:
            tags = []
        try:
            themes = json.loads(r[5]) if r[5] else []
        except Exception:
            themes = []
        results.append({
            "ts": r[0],
            "text": r[1],
            "sentiment": sent,
            "risk_tags": tags,
            "danger_level": r[4],
            "themes": themes,
        })
    return results


def save_daily_summary(user_id: str, date_str: str, themes: List[str], avg_sentiment: dict, risk_counts: dict, danger_summary: str, summary_text: str = None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO daily_summaries (user_id, date, themes_json, avg_sentiment_json, risk_counts_json, danger_summary, summary_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            date_str,
            json.dumps(themes),
            json.dumps(avg_sentiment),
            json.dumps(risk_counts),
            danger_summary,
            summary_text,
            datetime.utcnow().isoformat(),
        ),
    )
    conn.commit()
    conn.close()


def get_daily_summary(user_id: str, date_str: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT date, themes_json, avg_sentiment_json, risk_counts_json, danger_summary, summary_text, created_at FROM daily_summaries WHERE user_id = ? AND date = ? ORDER BY id DESC LIMIT 1",
        (user_id, date_str),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "date": row[0],
        "themes": json.loads(row[1]) if row[1] else [],
        "avg_sentiment": json.loads(row[2]) if row[2] else None,
        "risk_counts": json.loads(row[3]) if row[3] else {},
        "danger_summary": row[4],
        "summary_text": row[5],
        "created_at": row[6],
    }


def get_user_themes(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT date, themes_json FROM themes WHERE user_id = ? ORDER BY id DESC", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return [{"date": r[0], "themes": json.loads(r[1])} for r in rows]


# Ensure DB initialized
init_db()
