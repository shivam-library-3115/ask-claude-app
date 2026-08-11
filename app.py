import os
import time
from collections import defaultdict, deque

from flask import Flask, request, Response, render_template, stream_with_context
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

API_KEY = os.environ.get("ANTHROPIC_API_KEY")
if not API_KEY:
    print("WARNING: ANTHROPIC_API_KEY is not set. Add it to a .env file or your environment.")

client = Anthropic(api_key=API_KEY)

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
SYSTEM_PROMPT = "You are a helpful, concise assistant answering questions on a public web page."
MAX_TOKENS = 1024
MAX_MESSAGES = 40           # cap on conversation length (user + assistant turns)
MAX_MESSAGE_CHARS = 4000    # cap per message
MAX_TOTAL_CHARS = 20000     # cap on the whole conversation payload

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
            with client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=messages,
            ) as stream:
                for text in stream.text_stream:
                    yield f"data: {sse_escape(text)}\n\n"
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
