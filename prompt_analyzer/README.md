# prompt_analyzer — Project API (POC)

This is a small FastAPI proof-of-concept service that analyzes chat-like messages, stores per-message analyses, aggregates daily metrics, and can email human-friendly summaries.

Run the server (development)

```bash
cd prompt_analyzer
uvicorn main:app --reload
```

Notes
- Default host: http://127.0.0.1:8000
- Database: `themes.db` in the project root (auto-created)
- This repository currently runs as a POC: `/analyze` accepts `user_id` from the request body and does not require a JWT. This is insecure and intended for testing only.

Environment variables
- `SENDGRID_API_KEY`, `SENDGRID_FROM`, (optional) `SENDGRID_TO` — required for `POST /email_summary/{user_id}` to send emails.
- `DEFAULT_USER_ID` — fallback user id when none provided (defaults to `default_user`).
- `STORE_FULL_TEXT` — set to `1`, `true`, or `yes` to persist full message text (defaults to false).

Endpoints (current)

1) POST /analyze
- Purpose: Analyze one or more messages, return sentiment, themes, and optionally persist themes and analyses when `user_id` provided in the body.
- Payload:
  {
    "messages": [{"sender": "user","text": "..."}, ...],
    "user_id": "alice"  # optional for POC
  }
- Sample curl:

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"sender":"user","text":"I feel sad today"}],"user_id":"alice"}'
```

2) GET /analyses/{user_id}
- Return raw analysis rows for a user (optionally filtered by date query param `?date=YYYY-MM-DD`).

3) GET /mental_health/{user_id}
- Aggregate analyses for the user and ask the optional LLM helper to generate a human-friendly assessment. Requires LLM API key to uplevel.

4) POST /email_summary/{user_id}
- Compose an HTML + plaintext summary for the user's aggregated metrics and send via SendGrid. Body params: `recipient` (email) and optional `date` (YYYY-MM-DD). Requires SendGrid env vars.
- Sample curl (send today's summary to the same email):

```bash
curl -i -X POST "http://127.0.0.1:8000/email_summary/test%40gmail.com" \
  -H "Content-Type: application/json" \
  -d '{"recipient":"sample@gmail.com","date":"2025-09-19"}'
```

5) Other utilities
- There are helper modules for themes storage and LLM upleveling. See code in `app/` for additional endpoints and internal helpers.

Security & next steps
- This POC accepts `user_id` from the request body and should not be used as-is in production — a malicious client could spoof user IDs.
- For production: reintroduce JWT-based auth, exchange provider codes on the backend for local JWTs, and validate tokens on every request.
