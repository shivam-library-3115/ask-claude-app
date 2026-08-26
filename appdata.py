"""
App-data storage: user accounts, chat history, and usage logs.

This uses a SEPARATE database connection from db.py on purpose. db.py connects
with a READ-ONLY user to your analytics data and must stay that way. This module
connects with an app-data user that can write, but only ever touches its own
three tables (plush_users, plush_chat_history, plush_usage_log) — it never reads
or writes your analytics tables.

Config via env vars, falling back to the main DB_* vars' host/port if the
app-data-specific ones aren't set:
  APPDB_HOST / APPDB_PORT / APPDB_USER / APPDB_PASSWORD / APPDB_NAME
"""

import os
import datetime

from sqlalchemy import create_engine, text
from urllib.parse import quote_plus
from werkzeug.security import generate_password_hash, check_password_hash

_engine = None


def get_engine():
    global _engine
    if _engine is not None:
        return _engine

    host = os.environ.get("APPDB_HOST") or os.environ.get("DB_HOST")
    port = os.environ.get("APPDB_PORT") or os.environ.get("DB_PORT", "3306")
    user = os.environ.get("APPDB_USER")
    password = os.environ.get("APPDB_PASSWORD")
    name = os.environ.get("APPDB_NAME") or os.environ.get("DB_NAME")

    missing = [
        v for v, val in
        [("APPDB_USER", user), ("APPDB_PASSWORD", password)]
        if not val
    ]
    if missing or not host or not name:
        raise RuntimeError(
            "App-data storage is not configured. Set APPDB_USER and APPDB_PASSWORD "
            "(and APPDB_HOST/APPDB_NAME if different from your analytics DB)."
        )

    uri = f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{name}"
    _engine = create_engine(uri, pool_pre_ping=True, pool_recycle=280,
                            connect_args={"connect_timeout": 5})
    return _engine


def init_tables():
    """Create the app's own tables if they don't exist. Safe to call on every startup."""
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS plush_users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(64) NOT NULL UNIQUE,
                password_hash VARCHAR(255) NOT NULL,
                is_admin TINYINT(1) NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL
            ) CHARACTER SET utf8mb4
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS plush_chat_history (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(64) NOT NULL,
                chat_date DATE NOT NULL,
                question MEDIUMTEXT NOT NULL,
                answer MEDIUMTEXT,
                created_at DATETIME NOT NULL,
                INDEX idx_user_date (username, chat_date)
            ) CHARACTER SET utf8mb4
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS plush_usage_log (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(64) NOT NULL,
                model VARCHAR(64),
                input_tokens INT NOT NULL DEFAULT 0,
                output_tokens INT NOT NULL DEFAULT 0,
                cost_inr DECIMAL(12,4) NOT NULL DEFAULT 0,
                question MEDIUMTEXT,
                created_at DATETIME NOT NULL,
                INDEX idx_user (username),
                INDEX idx_created (created_at)
            ) CHARACTER SET utf8mb4
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS plush_table_access (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(64) NOT NULL,
                table_name VARCHAR(128) NOT NULL,
                UNIQUE KEY uq_user_table (username, table_name)
            ) CHARACTER SET utf8mb4
        """))


# ---------------- Users ----------------

def create_user(username, password, is_admin=False):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO plush_users (username, password_hash, is_admin, created_at) "
                 "VALUES (:u, :p, :a, :t)"),
            {"u": username.strip(), "p": generate_password_hash(password),
             "a": 1 if is_admin else 0, "t": datetime.datetime.utcnow()},
        )


def verify_user(username, password):
    """Returns dict {username, is_admin} on success, else None."""
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT username, password_hash, is_admin FROM plush_users WHERE username = :u"),
            {"u": (username or "").strip()},
        ).fetchone()
    if row and check_password_hash(row.password_hash, password):
        return {"username": row.username, "is_admin": bool(row.is_admin)}
    return None


def user_exists(username):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM plush_users WHERE username = :u"),
            {"u": (username or "").strip()},
        ).fetchone()
    return row is not None


def list_users():
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT username, is_admin, created_at FROM plush_users ORDER BY created_at")
        ).fetchall()
    return [{"username": r.username, "is_admin": bool(r.is_admin), "created_at": str(r.created_at)} for r in rows]


# ---------------- Chat history ----------------

def save_chat(username, question, answer):
    engine = get_engine()
    now = datetime.datetime.utcnow()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO plush_chat_history (username, chat_date, question, answer, created_at) "
                 "VALUES (:u, :d, :q, :a, :t)"),
            {"u": username, "d": now.date(), "q": question, "a": answer, "t": now},
        )


def list_chat_dates(username):
    """Distinct dates this user has history for, newest first."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT chat_date, COUNT(*) AS n FROM plush_chat_history "
                 "WHERE username = :u GROUP BY chat_date ORDER BY chat_date DESC"),
            {"u": username},
        ).fetchall()
    return [{"date": str(r.chat_date), "count": r.n} for r in rows]


def get_chat_for_date(username, chat_date):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT question, answer, created_at FROM plush_chat_history "
                 "WHERE username = :u AND chat_date = :d ORDER BY created_at"),
            {"u": username, "d": chat_date},
        ).fetchall()
    return [{"question": r.question, "answer": r.answer, "created_at": str(r.created_at)} for r in rows]


# ---------------- Usage log ----------------

def log_usage(username, model, input_tokens, output_tokens, cost_inr, question):
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO plush_usage_log (username, model, input_tokens, output_tokens, "
                 "cost_inr, question, created_at) VALUES (:u, :m, :i, :o, :c, :q, :t)"),
            {"u": username, "m": model, "i": input_tokens, "o": output_tokens,
             "c": cost_inr, "q": question, "t": datetime.datetime.utcnow()},
        )


def usage_summary():
    """Per-user totals for the admin view."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT username, COUNT(*) AS questions, "
            "SUM(input_tokens) AS input_tokens, SUM(output_tokens) AS output_tokens, "
            "SUM(cost_inr) AS cost_inr FROM plush_usage_log GROUP BY username ORDER BY cost_inr DESC"
        )).fetchall()
    return [{"username": r.username, "questions": r.questions,
             "input_tokens": int(r.input_tokens or 0), "output_tokens": int(r.output_tokens or 0),
             "cost_inr": float(r.cost_inr or 0)} for r in rows]


def usage_log(limit=500):
    """Full question-by-question log for the admin view, newest first."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT username, model, input_tokens, output_tokens, cost_inr, question, created_at "
                 "FROM plush_usage_log ORDER BY created_at DESC LIMIT :lim"),
            {"lim": limit},
        ).fetchall()
    return [{"username": r.username, "model": r.model, "input_tokens": r.input_tokens,
             "output_tokens": r.output_tokens, "cost_inr": float(r.cost_inr or 0),
             "question": r.question, "created_at": str(r.created_at)} for r in rows]


# ---------------- Table access permissions ----------------

def set_table_access(username, table_names):
    """Replace a user's allowed-table list with exactly table_names (a list of
    strings). An empty list means the user has access to NO tables until assigned."""
    engine = get_engine()
    # De-dupe and clean in Python so we don't depend on DB-specific INSERT IGNORE.
    clean = []
    seen = set()
    for t in table_names:
        t = (t or "").strip()
        if t and t not in seen:
            seen.add(t)
            clean.append(t)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM plush_table_access WHERE username = :u"), {"u": username})
        for t in clean:
            conn.execute(
                text("INSERT INTO plush_table_access (username, table_name) VALUES (:u, :t)"),
                {"u": username, "t": t},
            )


def get_table_access(username):
    """Return the set of table names this user is allowed to query."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT table_name FROM plush_table_access WHERE username = :u"),
            {"u": username},
        ).fetchall()
    return {r.table_name for r in rows}


def get_all_table_access():
    """Return {username: [table, ...]} for every user that has any assignment.
    Used by the admin view."""
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT username, table_name FROM plush_table_access ORDER BY username, table_name")
        ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r.username, []).append(r.table_name)
    return out
