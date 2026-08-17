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
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|"
    r"replace|call|exec|execute|merge|into\s+outfile|load_file)\b",
    re.IGNORECASE,
)
_LIMIT_RE = re.compile(r"\blimit\s+\d+", re.IGNORECASE)
_SELECT_OR_CTE = re.compile(r"^(select|with)\b", re.IGNORECASE)

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


def get_schema_context(max_tables: int = 40, max_cols_per_table: int = 25) -> str:
    """Return a compact text description of the database's tables and columns,
    used to tell Claude what it's allowed to query. Cached for
    SCHEMA_CACHE_TTL_SECONDS so new tables/columns show up on their own,
    without needing a server restart."""
    global _schema_cache, _schema_cache_time
    now = time.time()
    if _schema_cache is not None and (now - _schema_cache_time) < SCHEMA_CACHE_TTL_SECONDS:
        return _schema_cache

    engine = get_engine()
    insp = inspect(engine)
    tables = insp.get_table_names()

    lines = []
    for table_name in tables[:max_tables]:
        cols = insp.get_columns(table_name)
        col_desc = ", ".join(f"{c['name']} ({c['type']})" for c in cols[:max_cols_per_table])
        lines.append(f"- {table_name}: {col_desc}")

    schema_text = "\n".join(lines) if lines else "(no tables visible to this database user)"
    if len(tables) > max_tables:
        schema_text += f"\n… and {len(tables) - max_tables} more tables not shown."

    _schema_cache = schema_text
    _schema_cache_time = now
    return _schema_cache


def validate_select(query: str):
    """Returns (safe_query, error). Only ever allows a single SELECT/WITH statement."""
    q = (query or "").strip().rstrip(";").strip()
    if not q:
        return None, "Empty query."
    if ";" in q:
        return None, "Only a single statement is allowed (no semicolons)."
    if not _SELECT_OR_CTE.match(q):
        return None, "Only SELECT statements are allowed."
    if _FORBIDDEN.search(q):
        return None, "Query contains a disallowed keyword."
    if not _LIMIT_RE.search(q):
        q = f"{q} LIMIT {ROW_CAP}"
    return q, None


def run_sql_query(query: str) -> dict:
    """Validate and execute a read-only query. Always returns a JSON-serializable dict."""
    safe_query, err = validate_select(query)
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
