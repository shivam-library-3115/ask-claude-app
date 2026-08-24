import json
import os
import queue
import threading
import time
from collections import defaultdict, deque

from flask import Flask, request, Response, render_template, stream_with_context, session, redirect, url_for
from anthropic import Anthropic
from dotenv import load_dotenv

import db

load_dotenv()

app = Flask(__name__)

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not API_KEY:
    print("WARNING: ANTHROPIC_API_KEY is not set. Add it to a .env file or your environment.")

client = Anthropic(api_key=API_KEY, max_retries=4)

HEARTBEAT_INTERVAL_SECONDS = 10  # comfortably below any real-world idle-connection timeout


def iter_with_heartbeat(iterable, interval=HEARTBEAT_INTERVAL_SECONDS):
    """Wrap a slow-to-start, blocking iterable (e.g. stream.text_stream) so
    that any gap longer than `interval` seconds yields ("heartbeat", None)
    instead of leaving the connection silent. Real items are relayed with
    no added latency the moment they arrive — this never slows down actual
    streaming, it only fills in genuine silence. Runs the real iteration on
    a background thread and relays it through a queue."""
    q = queue.Queue()

    def producer():
        try:
            for item in iterable:
                q.put(("item", item))
        except Exception as exc:
            q.put(("error", exc))
        finally:
            q.put(("done", None))

    threading.Thread(target=producer, daemon=True).start()

    while True:
        try:
            kind, value = q.get(timeout=interval)
        except queue.Empty:
            yield ("heartbeat", None)
            continue
        if kind == "done":
            return
        yield (kind, value)


def call_with_heartbeat(fn, args=(), kwargs=None, interval=HEARTBEAT_INTERVAL_SECONDS):
    """Same idea as iter_with_heartbeat, for a single blocking call (e.g. a
    slow SQL query) instead of a stream. Yields ("heartbeat", None) during
    any wait longer than `interval` seconds, then ("result", value) once
    the call actually finishes."""
    kwargs = kwargs or {}
    q = queue.Queue()

    def worker():
        try:
            q.put(("result", fn(*args, **kwargs)))
        except Exception as exc:
            q.put(("error", exc))

    threading.Thread(target=worker, daemon=True).start()

    while True:
        try:
            kind, value = q.get(timeout=interval)
        except queue.Empty:
            yield ("heartbeat", None)
            continue
        if kind == "error":
            raise value
        yield ("result", value)
        return

SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    print("WARNING: SECRET_KEY is not set — using a random key that changes on every "
          "restart, which will log everyone out on every deploy. Set SECRET_KEY to "
          "any long random string to avoid that.")
    SECRET_KEY = os.urandom(32).hex()
app.secret_key = SECRET_KEY

SITE_PASSWORD = os.environ.get("SITE_PASSWORD")
if not SITE_PASSWORD:
    print("WARNING: SITE_PASSWORD is not set — the site is NOT password protected.")

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

# USD price per million tokens. Covers the models this app is likely to use;
# unrecognized models fall back to Sonnet 5 pricing. Verify/update at
# https://platform.claude.com/docs/en/about-claude/pricing if you switch models
# or Anthropic's rates change.
MODEL_PRICING_USD_PER_MTOK = {
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0},
    "claude-fable-5": {"input": 10.0, "output": 50.0},
}
DEFAULT_PRICING = MODEL_PRICING_USD_PER_MTOK["claude-sonnet-5"]

# Approximate — exchange rates drift. Override with a real-time source, or
# just update this number occasionally, via the USD_TO_INR_RATE env var.
USD_TO_INR_RATE = float(os.environ.get("USD_TO_INR_RATE", "95.6"))


def load_schema_notes():
    """Load the human-maintained business/domain notes about the database, if
    present. Returns "" when the file is absent so the app still works without it."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema_notes.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except Exception as exc:
        print(f"WARNING: could not read schema_notes.md ({exc}) — continuing without it.")
        return ""


SCHEMA_NOTES = load_schema_notes()
MAX_TOKENS = 30000
MAX_MESSAGES = 1000          # cap on conversation length (user + assistant turns)
MAX_MESSAGE_CHARS = 250000   # cap per message
MAX_TOTAL_CHARS = 200000    # cap on the whole conversation payload
MAX_TOOL_STEPS = 150         # cap on how many query/render round-trips one question can take

MAX_CHART_SERIES = 10
MAX_CHART_POINTS = 500
MAX_TABLE_ROWS = 500
MAX_TABLE_COLS = 30

SQL_TOOL = {
    "name": "run_sql_query",
    "description": (
        "Run a single read-only SQL SELECT statement against the connected "
        "MySQL database and return the matching rows. Use it to look up "
        "whatever data you need to answer the question. You may call it "
        "more than once — for example, to check a table's shape before "
        "writing the real query, or to refine a query after seeing results."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A single SELECT (or WITH ... SELECT) statement. No INSERT/UPDATE/DELETE/DDL.",
            }
        },
        "required": ["query"],
    },
}

CHART_TOOL = {
    "name": "render_chart",
    "description": (
        "Display a chart to the user, after you've already looked up the data with "
        "run_sql_query. Use 'line' for a trend over time, 'bar' for comparing "
        "categories, 'pie' for a share-of-total breakdown, and 'scatter' for the "
        "relationship between two numeric values. Prefer this over describing "
        "numbers in prose whenever the question is naturally visual."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "chart_type": {"type": "string", "enum": ["line", "bar", "pie", "scatter"]},
            "title": {"type": "string"},
            "labels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Category labels for the x-axis (line/bar) or slice names (pie). Omit for scatter.",
            },
            "series": {
                "type": "array",
                "description": "One or more data series to plot.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "data": {
                            "description": (
                                "For line/bar/pie: an array of numbers, same length and "
                                "order as 'labels'. For scatter: an array of {x, y} objects."
                            )
                        },
                    },
                    "required": ["name", "data"],
                },
            },
        },
        "required": ["chart_type", "series"],
    },
}

TABLE_TOOL = {
    "name": "render_table",
    "description": (
        "Display query results as a readable table with columns and rows, instead "
        "of writing a list of records out as text. Use this whenever the answer is "
        "naturally multiple rows of structured data."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {
                "type": "array",
                "description": "Each item is one row: an array of cell values, in the same order as 'columns'.",
                "items": {"type": "array"},
            },
            "highlight_rows": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Optional. 0-indexed row numbers to visually flag — for example, "
                    "weekend dates when the user asks to highlight weekends."
                ),
            },
        },
        "required": ["columns", "rows"],
    },
}

# --- Very basic per-IP rate limiting (in-memory, single-process only) ---
# Good enough to stop casual abuse; use Flask-Limiter or a reverse-proxy
# rule (nginx/Cloudflare) for anything with real public traffic.
RATE_LIMIT = 10                # /ask requests
RATE_WINDOW_SECONDS = 60       # per this many seconds
LOGIN_RATE_LIMIT = 5           # login attempts
LOGIN_RATE_WINDOW_SECONDS = 300  # per this many seconds
_request_log = defaultdict(deque)


def is_rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    now = time.time()
    log = _request_log[key]
    while log and now - log[0] > window_seconds:
        log.popleft()
    if len(log) >= limit:
        return True
    log.append(now)
    return False


def sse_escape(text: str) -> str:
    # SSE "data:" fields can't contain raw newlines, so encode them
    # and decode again on the client. Only used for plain text — chart/table
    # payloads are JSON, which is already single-line safe, and must NOT be
    # run through this or the client's matching decode would corrupt them.
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def validate_messages(raw):
    """Validate the client-supplied conversation array. Returns (messages, error)."""
    if not isinstance(raw, list) or not raw:
        return None, "Please include a non-empty 'messages' array."
    if len(raw) > MAX_MESSAGES:
        return None, f"Conversation is too long (max {MAX_MESSAGES} turns) — start a new one."

    cleaned = []
    total_chars = 0
    for item in raw:
        if not isinstance(item, dict):
            return None, "Each message must be an object with 'role' and 'content'."
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            return None, "Each message needs role 'user' or 'assistant' and string content."
        content = content.strip()
        if not content:
            return None, "Message content can't be empty."
        if len(content) > MAX_MESSAGE_CHARS:
            return None, f"A message is too long (max {MAX_MESSAGE_CHARS} characters)."
        total_chars += len(content)
        cleaned.append({"role": role, "content": content})

    if total_chars > MAX_TOTAL_CHARS:
        return None, "Conversation is too long overall — start a new one."
    if cleaned[-1]["role"] != "user":
        return None, "The last message must be from the user."

    return cleaned, None


def build_system_and_tools():
    """Schema-aware system prompt + tools — degrades gracefully to a plain
    assistant (no tools) if the database can't be reached right now."""
    try:
        schema = db.get_schema_context()
        system = (
            "You are Plush Buddy, the data assistant for Plush Intelligence. "
            "Use the run_sql_query tool to look up real data before answering — "
            "never guess at numbers or rows. Only the tables and columns listed "
            "below exist; if a question needs something outside them, say so "
            "instead of inventing it.\n\n"
            "Default the year to 2026 whenever a question names a month without a "
            "year (e.g. \"Jun\" means June 2026), unless another year is stated.\n\n"
            "For any result that has data behind it, show BOTH a table and a chart: "
            "call render_table AND render_chart. Use render_chart for the trend/"
            "comparison/breakdown and render_table for the underlying rows — use "
            "highlight_rows to flag rows the user cares about (weekends, outliers, "
            "a particular category). Keep the written portion to a few precise "
            "bullet points — no long paragraphs, and don't repeat the full data as "
            "text when it's already in the table/chart.\n\n"
            "Never mention SQL, queries, or table/column names in your answer — "
            "the person you're talking to should just see the result.\n\n"
            "When writing numbers, whether in prose or in charts/tables: amounts "
            "and quantities (sales, revenue, units, counts) are whole numbers "
            "with comma separators and no decimals, e.g. 12,450. Calculated "
            "rates and ratios (discount %, CTR, CVR, margin, conversion rate) "
            "get up to 2 decimal places, e.g. 4.37%. Name chart/table columns "
            "so this is obvious — include the % sign or words like \"rate\"/"
            "\"CTR\"/\"discount\" for ratio-type columns.\n\n"
            "Favor efficient SQL over many small queries: use GROUP BY, JOINs, "
            "UNION, or CTEs to answer in one or two queries rather than looping "
            "one query per category, channel, or time period — you have a "
            "limited number of tool-use steps per question.\n\n"
            + (
                "Business context about this database — trust these definitions "
                "over your own assumptions about what a column means:\n"
                + SCHEMA_NOTES + "\n\n"
                if SCHEMA_NOTES else ""
            )
            + "Live database schema (actual tables and columns):\n" + schema
        )
        return system, [SQL_TOOL, CHART_TOOL, TABLE_TOOL]
    except Exception as exc:
        system = (
            "You are Plush Buddy, the data assistant for Plush Intelligence. "
            f"The connected database is currently unavailable ({exc}). Tell "
            "the user their database can't be reached right now rather than "
            "guessing at any data."
        )
        return system, []


def prepare_chart(raw_input: dict):
    """Validate + cap a render_chart call. Returns (payload_for_client, tool_result)."""
    chart_type = raw_input.get("chart_type")
    if chart_type not in ("line", "bar", "pie", "scatter"):
        return None, {"error": "chart_type must be one of: line, bar, pie, scatter."}

    series = raw_input.get("series")
    if not isinstance(series, list) or not series:
        return None, {"error": "series must be a non-empty array."}

    trimmed_series = []
    for s in series[:MAX_CHART_SERIES]:
        if not isinstance(s, dict):
            continue
        data = s.get("data")
        if isinstance(data, list):
            data = data[:MAX_CHART_POINTS]
        trimmed_series.append({"name": s.get("name", ""), "data": data})

    if not trimmed_series:
        return None, {"error": "No valid series provided."}

    payload = {
        "chart_type": chart_type,
        "title": raw_input.get("title", ""),
        "labels": (raw_input.get("labels") or [])[:MAX_CHART_POINTS],
        "series": trimmed_series,
    }
    return payload, {"status": "Chart displayed to the user."}


def prepare_table(raw_input: dict):
    """Validate + cap a render_table call. Returns (payload_for_client, tool_result)."""
    columns = raw_input.get("columns")
    rows = raw_input.get("rows")
    if not isinstance(columns, list) or not columns:
        return None, {"error": "columns must be a non-empty array."}
    if not isinstance(rows, list):
        return None, {"error": "rows must be an array."}

    truncated = len(rows) > MAX_TABLE_ROWS
    kept_rows = rows[:MAX_TABLE_ROWS]

    raw_highlights = raw_input.get("highlight_rows")
    highlight_rows = []
    if isinstance(raw_highlights, list):
        highlight_rows = sorted({
            i for i in raw_highlights
            if isinstance(i, int) and 0 <= i < len(kept_rows)
        })

    payload = {
        "title": raw_input.get("title", ""),
        "columns": columns[:MAX_TABLE_COLS],
        "rows": kept_rows,
        "highlight_rows": highlight_rows,
    }
    status = "Table displayed to the user."
    if truncated:
        status += f" (truncated to the first {MAX_TABLE_ROWS} rows)"
    return payload, {"status": status}


@app.before_request
def require_login():
    if not SITE_PASSWORD:
        return  # no password configured — app stays open
    if request.endpoint in ("login", "static"):
        return
    if session.get("authenticated"):
        return
    if request.endpoint == "ask":
        return {"error": "Session expired — please refresh the page and log in again."}, 401
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
        if is_rate_limited(f"login:{ip}", LOGIN_RATE_LIMIT, LOGIN_RATE_WINDOW_SECONDS):
            error = "Too many attempts. Please wait a few minutes and try again."
        elif SITE_PASSWORD and request.form.get("password") == SITE_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("index"))
        else:
            error = "Incorrect password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    return render_template("index.html", auth_enabled=bool(SITE_PASSWORD), default_model=MODEL)


@app.route("/ask", methods=["POST"])
def ask():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    if is_rate_limited(f"ask:{ip}", RATE_LIMIT, RATE_WINDOW_SECONDS):
        return {"error": "Too many requests. Please wait a bit and try again."}, 429

    data = request.get_json(silent=True) or {}
    messages, error = validate_messages(data.get("messages"))
    if error:
        return {"error": error}, 400
    if not API_KEY:
        return {"error": "Server is missing ANTHROPIC_API_KEY."}, 500

    requested_model = data.get("model")
    selected_model = requested_model if requested_model in MODEL_PRICING_USD_PER_MTOK else MODEL

    def generate():
        try:
            system, tools = build_system_and_tools()
            convo = list(messages)  # local copy — tool turns never go back to the client
            total_input_tokens = 0
            total_output_tokens = 0

            for step in range(MAX_TOOL_STEPS):
                yield f"event: status\ndata: {sse_escape('Reviewing results…' if step else 'Thinking…')}\n\n"

                stream_kwargs = dict(model=selected_model, max_tokens=MAX_TOKENS, system=system, messages=convo)
                if tools:
                    stream_kwargs["tools"] = tools

                with client.messages.stream(**stream_kwargs) as stream:
                    for kind, value in iter_with_heartbeat(stream.text_stream, interval=HEARTBEAT_INTERVAL_SECONDS):
                        if kind == "heartbeat":
                            yield ": keepalive\n\n"  # SSE comment line — ignored by the client, keeps the connection visibly alive
                        elif kind == "error":
                            raise value
                        else:
                            yield f"data: {sse_escape(value)}\n\n"
                    final = stream.get_final_message()

                usage = getattr(final, "usage", None)
                if usage:
                    total_input_tokens += usage.input_tokens
                    total_output_tokens += usage.output_tokens

                if final.stop_reason != "tool_use":
                    break

                tool_use_blocks = [b for b in final.content if b.type == "tool_use"]
                convo.append({"role": "assistant", "content": final.content})

                tool_results = []
                for block in tool_use_blocks:
                    if block.name == "run_sql_query":
                        query = (block.input or {}).get("query", "")
                        # The query itself stays server-side by design — the
                        # person using the page only ever sees this status.
                        yield f"event: status\ndata: {sse_escape('Fetching data…')}\n\n"
                        result = None
                        for kind, value in call_with_heartbeat(db.run_sql_query, args=(query,), interval=HEARTBEAT_INTERVAL_SECONDS):
                            if kind == "heartbeat":
                                yield ": keepalive\n\n"
                            else:
                                result = value
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str)[:8000],
                            "is_error": bool(isinstance(result, dict) and result.get("error")),
                        })

                    elif block.name == "render_chart":
                        yield f"event: status\ndata: {sse_escape('Building chart…')}\n\n"
                        payload, result = prepare_chart(block.input or {})
                        if payload is not None:
                            # JSON is already single-line-safe — do NOT sse_escape this,
                            # that's only for the plain-text path.
                            yield f"event: chart\ndata: {json.dumps(payload, default=str)}\n\n"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                            "is_error": bool("error" in result),
                        })

                    elif block.name == "render_table":
                        yield f"event: status\ndata: {sse_escape('Building table…')}\n\n"
                        payload, result = prepare_table(block.input or {})
                        if payload is not None:
                            yield f"event: table\ndata: {json.dumps(payload, default=str)}\n\n"
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                            "is_error": bool("error" in result),
                        })

                    else:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps({"error": f"Unknown tool: {block.name}"}),
                            "is_error": True,
                        })

                convo.append({"role": "user", "content": tool_results})
            else:
                yield f"event: error\ndata: {sse_escape('This question needed too many steps — try narrowing it.')}\n\n"
                return

            pricing = MODEL_PRICING_USD_PER_MTOK.get(selected_model, DEFAULT_PRICING)
            cost_usd = (total_input_tokens / 1_000_000 * pricing["input"]) + (
                total_output_tokens / 1_000_000 * pricing["output"]
            )
            usage_payload = {
                "model": selected_model,
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "cost_inr": round(cost_usd * USD_TO_INR_RATE, 4),
            }
            yield f"event: usage\ndata: {json.dumps(usage_payload)}\n\n"
            yield "event: done\ndata: {}\n\n"
        except Exception as exc:
            yield f"event: error\ndata: {sse_escape(str(exc))}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
