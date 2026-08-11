import json
import os
import time
from collections import defaultdict, deque

from flask import Flask, request, Response, render_template, stream_with_context
from anthropic import Anthropic
from dotenv import load_dotenv

import db

load_dotenv()

app = Flask(__name__)

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not API_KEY:
    print("WARNING: ANTHROPIC_API_KEY is not set. Add it to a .env file or your environment.")

client = Anthropic(api_key=API_KEY)

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
MAX_TOKENS = 1500
MAX_MESSAGES = 40           # cap on conversation length (user + assistant turns)
MAX_MESSAGE_CHARS = 4000    # cap per message
MAX_TOTAL_CHARS = 20000     # cap on the whole conversation payload
MAX_TOOL_STEPS = 5          # cap on how many query round-trips one question can take

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

# --- Very basic per-IP rate limiting (in-memory, single-process only) ---
# Good enough to stop casual abuse; use Flask-Limiter or a reverse-proxy
# rule (nginx/Cloudflare) for anything with real public traffic.
RATE_LIMIT = 10          # requests
RATE_WINDOW_SECONDS = 60 # per this many seconds
_request_log = defaultdict(deque)


def is_rate_limited(ip: str) -> bool:
    now = time.time()
    log = _request_log[ip]
    while log and now - log[0] > RATE_WINDOW_SECONDS:
        log.popleft()
    if len(log) >= RATE_LIMIT:
        return True
    log.append(now)
    return False


def sse_escape(text: str) -> str:
    # SSE "data:" fields can't contain raw newlines, so encode them
    # and decode again on the client.
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
    """Schema-aware system prompt + the SQL tool — degrades gracefully to a
    plain assistant (no tool) if the database can't be reached right now."""
    try:
        schema = db.get_schema_context()
        system = (
            "You are a helpful data analyst answering questions about a MySQL "
            "database. Use the run_sql_query tool to look up real data before "
            "answering — never guess at numbers or rows. Only the tables and "
            "columns listed below exist; if a question needs something outside "
            "them, say so instead of inventing it. Keep answers concise and, "
            "where useful, summarize what the query found rather than dumping "
            "raw rows.\n\nDatabase schema:\n" + schema
        )
        return system, [SQL_TOOL]
    except Exception as exc:
        system = (
            "You are a helpful assistant. The connected database is currently "
            f"unavailable ({exc}). Tell the user their database can't be "
            "reached right now rather than guessing at any data."
        )
        return system, []


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/ask", methods=["POST"])
def ask():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    if is_rate_limited(ip):
        return {"error": "Too many requests. Please wait a bit and try again."}, 429

    data = request.get_json(silent=True) or {}
    messages, error = validate_messages(data.get("messages"))
    if error:
        return {"error": error}, 400
    if not API_KEY:
        return {"error": "Server is missing ANTHROPIC_API_KEY."}, 500

    def generate():
        try:
            system, tools = build_system_and_tools()
            convo = list(messages)  # local copy — tool turns never go back to the client

            for step in range(MAX_TOOL_STEPS):
                yield f"event: status\ndata: {sse_escape('Reviewing results…' if step else 'Thinking…')}\n\n"

                stream_kwargs = dict(model=MODEL, max_tokens=MAX_TOKENS, system=system, messages=convo)
                if tools:
                    stream_kwargs["tools"] = tools

                with client.messages.stream(**stream_kwargs) as stream:
                    for chunk in stream.text_stream:
                        yield f"data: {sse_escape(chunk)}\n\n"
                    final = stream.get_final_message()

                if final.stop_reason != "tool_use":
                    break

                tool_use_blocks = [b for b in final.content if b.type == "tool_use"]
                convo.append({"role": "assistant", "content": final.content})

                tool_results = []
                for block in tool_use_blocks:
                    query = (block.input or {}).get("query", "")
                    yield f"event: status\ndata: {sse_escape('Running: ' + query)}\n\n"
                    result = db.run_sql_query(query)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str)[:8000],
                        "is_error": bool(isinstance(result, dict) and result.get("error")),
                    })
                convo.append({"role": "user", "content": tool_results})
            else:
                yield f"event: error\ndata: {sse_escape('This question needed too many query steps — try narrowing it.')}\n\n"
                return

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
