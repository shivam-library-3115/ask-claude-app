import os
import re
import time
from urllib.parse import quote_plus

from sqlalchemy import create_engine, inspect, text

_engine = None
_schema_cache = None
_schema_cache_time = 0.0
SCHEMA_CACHE_TTL_SECONDS = 300  # re-check the real schema at most every 5 minutes

# Defense-in-depth on top of your read-only DB user: even if that user's
# grants were ever misconfigured, these checks stop write/DDL statements
# and multi-statement injection before a query ever reaches the database.
# Block write/DDL statements. Each is anchored so it only matches the KEYWORD
# form (a statement), not a same-named SQL function used inside a SELECT.
# e.g. REPLACE(col,' ','') is a harmless string function and must be allowed,
# while REPLACE INTO ... is a write and must be blocked.
_FORBIDDEN = re.compile(
    r"\b(insert\s+into|insert\s+ignore|update\s+\w|delete\s+from|drop\s+|"
    r"alter\s+|create\s+|truncate\s+|grant\s+|revoke\s+|replace\s+into|"
    r"call\s+|exec\s+|execute\s+|merge\s+into|into\s+outfile|into\s+dumpfile|load_file\s*\()",
    re.IGNORECASE,
)
_LIMIT_RE = re.compile(r"\blimit\s+\d+", re.IGNORECASE)
# Allow an optional opening parenthesis / whitespace before SELECT or WITH, so
# parenthesized queries like (SELECT ...) UNION (SELECT ...) are accepted.
_SELECT_OR_CTE = re.compile(r"^[\s(]*(select|with)\b", re.IGNORECASE)

ROW_CAP = 200


def get_engine():
    global _engine
    if _engine is None:
        host = os.environ.get("DB_HOST")
        port = os.environ.get("DB_PORT", "3306")
        user = os.environ.get("DB_USER")
        password = os.environ.get("DB_PASSWORD")
        name = os.environ.get("DB_NAME")
        missing = [
            var
            for var, val in [
                ("DB_HOST", host),
                ("DB_USER", user),
                ("DB_PASSWORD", password),
                ("DB_NAME", name),
            ]
            if not val
        ]
        if missing:
            raise RuntimeError(f"Missing database env vars: {', '.join(missing)}")

        uri = f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{name}"
        _engine = create_engine(
            uri,
            pool_pre_ping=True,
            pool_recycle=280,
            connect_args={"connect_timeout": 5},
        )
    return _engine


def get_all_table_names():
    """Return the full list of real table names in the database (uncached fetch)."""
    engine = get_engine()
    insp = inspect(engine)
    return insp.get_table_names()


def get_schema_context(max_tables: int = 200, max_cols_per_table: int = 200,
                       allowed_tables=None) -> str:
    """Return a compact text description of the database's tables and columns,
    used to tell Claude what it's allowed to query. Cached for
    SCHEMA_CACHE_TTL_SECONDS so new tables/columns show up on their own,
    without needing a server restart.

    If allowed_tables is a set, only those tables are described — so a
    restricted user's assistant never even sees tables they can't query.
    (Caching only applies to the unrestricted/full view.)"""
    global _schema_cache, _schema_cache_time
    restricted = allowed_tables is not None
    now = time.time()
    if not restricted and _schema_cache is not None and (now - _schema_cache_time) < SCHEMA_CACHE_TTL_SECONDS:
        return _schema_cache

    engine = get_engine()
    insp = inspect(engine)
    tables = insp.get_table_names()
    if restricted:
        allowed_lower = {t.lower() for t in allowed_tables}
        tables = [t for t in tables if t.lower() in allowed_lower]

    lines = []
    for table_name in tables[:max_tables]:
        cols = insp.get_columns(table_name)
        col_desc = ", ".join(f"{c['name']} ({c['type']})" for c in cols[:max_cols_per_table])
        lines.append(f"- {table_name}: {col_desc}")

    schema_text = "\n".join(lines) if lines else "(no tables available to you)"
    if len(tables) > max_tables:
        schema_text += f"\n… and {len(tables) - max_tables} more tables not shown."

    if not restricted:
        _schema_cache = schema_text
        _schema_cache_time = now
    return schema_text


def _tables_referenced(query, known_tables):
    """Best-effort: return the subset of known_tables whose name appears as a
    word in the query (case-insensitive). Used to enforce per-user access."""
    q_lower = query.lower()
    hits = set()
    for t in known_tables:
        # word-boundary match so 'orders' doesn't match 'orders_archive' partially
        if re.search(r'(?<![\w.])' + re.escape(t.lower()) + r'(?![\w])', q_lower):
            hits.add(t)
    return hits


def validate_select(query: str, allowed_tables=None, all_tables=None):
    """Returns (safe_query, error). Only ever allows a single SELECT/WITH statement.

    If allowed_tables is provided (a set), the query is additionally rejected if
    it references any real table NOT in that set — enforced server-side, so it's
    real access control, not a prompt suggestion."""
    q = (query or "").strip().rstrip(";").strip()
    if not q:
        return None, "Empty query."
    if ";" in q:
        return None, "Only a single statement is allowed (no semicolons)."
    if not _SELECT_OR_CTE.match(q):
        return None, "Only SELECT statements are allowed."
    if _FORBIDDEN.search(q):
        return None, "Query contains a disallowed keyword."

    if allowed_tables is not None:
        known = all_tables if all_tables is not None else get_all_table_names()
        referenced = _tables_referenced(q, known)
        allowed_lower = {t.lower() for t in allowed_tables}
        blocked = {t for t in referenced if t.lower() not in allowed_lower}
        if blocked:
            names = ", ".join(sorted(blocked))
            return None, (f"You don't have access to the following table(s): {names}. "
                          "Ask your admin to grant access if you need them.")

    if not _LIMIT_RE.search(q):
        q = f"{q} LIMIT {ROW_CAP}"
    return q, None


def run_sql_query(query: str, allowed_tables=None, all_tables=None) -> dict:
    """Validate and execute a read-only query. Always returns a JSON-serializable dict.
    allowed_tables (a set) restricts which tables the query may touch."""
    safe_query, err = validate_select(query, allowed_tables=allowed_tables, all_tables=all_tables)
    if err:
        return {"error": err}

    try:
        engine = get_engine()
        with engine.connect() as conn:
            try:
                # MySQL 8.0+ session-level statement timeout, in milliseconds.
                # Best-effort: older MySQL/MariaDB versions may not support it.
                conn.execute(text("SET SESSION max_execution_time = 5000"))
            except Exception:
                pass
            result = conn.execute(text(safe_query))
            rows = [dict(row._mapping) for row in result.fetchmany(ROW_CAP)]
        return {"rows": rows, "row_count": len(rows)}
    except Exception as exc:
        return {"error": str(exc)}
