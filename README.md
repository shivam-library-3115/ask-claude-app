# Ask Claude — Web Q&A Interface

A small Flask app: visitors type a question into a web page, it's sent to
your backend, your backend calls the Claude API, and the answer streams
back into the page token-by-token in real time (via Server-Sent Events).

```
Browser  --POST /ask-->  Flask backend  --stream-->  Claude API
   <---------------- SSE stream of text ----------------
```

Your API key never touches the browser — it lives only in the backend's
environment, which is the safe way to do this.

## 1. Get an API key

1. Go to https://console.anthropic.com and sign up / log in.
2. Open **API Keys** and create a new key.
3. Add a small amount of credit under **Billing** — the API is pay-as-you-go
   and is separate from any Claude.ai subscription.

## 2. Run it locally

```bash
cd ask-claude-app
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# then open .env and paste your real key into ANTHROPIC_API_KEY

python app.py
```

Visit http://localhost:5000 and ask something.

## 3. How it's built

- `app.py` — Flask route `/ask` calls `client.messages.stream(...)` from the
  official `anthropic` Python SDK and forwards each text chunk to the
  browser as an SSE event.
- `templates/index.html` + `static/style.css` — a "reference desk" card
  catalog: each question/answer pair renders as a numbered entry, in a
  warm-paper palette with a serif display face and a monospace face for
  the Q&A text.
- `static/script.js` — keeps the running conversation in a JS array,
  POSTs the whole thing to `/ask` on each question (so follow-ups have
  context), reads the SSE stream, and fills the answer in live,
  character-by-character, with a blinking cursor while it's still coming in.
- The backend validates that array on every request (role/content shape,
  per-message length, total conversation length) before sending it to
  Claude — `app.py`'s `validate_messages()`.
- A basic in-memory per-IP rate limiter (10 requests/minute) is included in
  `app.py` so a public page can't rack up API costs from casual abuse. It
  resets whenever the process restarts and doesn't work across multiple
  server instances — fine for a personal project, not for real production
  traffic (see the note below).
- Model defaults to `claude-sonnet-5`; change `CLAUDE_MODEL` in `.env` if
  you want `claude-opus-4-8` (stronger, pricier) or `claude-haiku-4-5-20251001`
  (fastest, cheapest).

## 4. Hosting it

Since you're still deciding, here are the two simplest paths:

**Option A — Managed platform (least setup)**
Render, Railway, or Fly.io can all run a Flask app directly from a GitHub
repo with a couple of clicks:
1. Push this folder to a GitHub repo.
2. Create a new "Web Service" (Render/Railway) pointing at that repo.
3. Set the start command to `gunicorn app:app` (add `gunicorn` to
   `requirements.txt` first — see below).
4. Add `ANTHROPIC_API_KEY` as an environment variable in the platform's
   dashboard (never commit it to the repo).

**Option B — Your own VPS**
1. Install Python, then repeat the steps in section 2 on the server.
2. Run it with a production server instead of Flask's dev server:
   ```bash
   pip install gunicorn
   gunicorn -w 2 -b 0.0.0.0:5000 app:app
   ```
3. Put nginx (or Caddy) in front of it for HTTPS and as a reverse proxy.
4. Keep the process alive with `systemd` or `pm2`.

Either way: **never** run with `FLASK_DEBUG=1` once it's public, and keep
the rate limiter (or a stronger one) on — an open text box wired to a
metered API is the main cost risk here.

## 5. Reasonable next steps

- Swap the in-memory rate limiter for `Flask-Limiter` with a Redis backend
  if you deploy to more than one server instance.
- Add simple auth (a shared password, or a login) if this isn't meant to be
  fully public.
- The conversation only lives in the browser tab's memory — add a "New
  question" button to clear `history` in `script.js` if you want an easy
  way to reset context.
