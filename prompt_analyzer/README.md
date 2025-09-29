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
- **Authentication required**: `/analyze` now requires a valid JWT in the Authorization header. Use the browser extension to authenticate via OAuth2/PKCE or call `/auth/exchange` to get a local JWT.

Environment variables
- `JWT_SECRET` — Secret key for signing/verifying local JWTs (defaults to `dev-secret-change-me` for development).
- `OAUTH_CLIENT_ID`, `OAUTH_CLIENT_SECRET` — OAuth2 credentials for `/auth/exchange` endpoint.
- `SENDGRID_API_KEY`, `SENDGRID_FROM`, (optional) `SENDGRID_TO` — required for `POST /email_summary/{user_id}` to send emails.
- `STORE_FULL_TEXT` — set to `1`, `true`, or `yes` to persist full message text (defaults to false).

Endpoints (current)

1) POST /analyze
- Purpose: Analyze one or more messages, return sentiment, themes, and persist analyses for the authenticated user.
- **Requires**: `Authorization: Bearer <local_jwt>` header
- Payload:
  {
    "messages": [{"sender": "user","text": "..."}, ...]
  }
- Sample curl (requires valid JWT from `/auth/exchange`):

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H "Authorization: Bearer <your_local_jwt>" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"sender":"user","text":"I feel sad today"}]}'
```

2) POST /auth/exchange
- Purpose: Exchange OAuth2 authorization code for a backend-issued local JWT
- Payload: { "code": "...", "code_verifier": "...", "redirect_uri": "..." }
- Returns: { "access_token": "<local_jwt>", "expires_in": 86400, "user_email": "..." }

2) GET /analyses/{user_id}
- Return raw analysis rows for a user (optionally filtered by date query param `?date=YYYY-MM-DD`).

3) GET /mental_health/{user_id}
- Aggregate analyses for the user and ask the optional LLM helper to generate a human-friendly assessment. Requires LLM API key to uplevel.

4) POST /email_summary/{user_id}
- Compose an HTML + plaintext summary for the user's aggregated metrics and send via SendGrid. The user_id is used as the email recipient. Body params: optional `date` (YYYY-MM-DD). Requires SendGrid env vars.
- Sample curl (send today's summary):

```bash
curl -i -X POST "http://127.0.0.1:8000/email_summary/test@gmail.com" \
  -H "Content-Type: application/json" \
  -d '{"date":"2025-09-29"}'
```

5) Other utilities
- There are helper modules for themes storage and LLM upleveling. See code in `app/` for additional endpoints and internal helpers.

Security & next steps
- **JWT Authentication**: `/analyze` now requires valid JWT authentication. Users must authenticate via the browser extension (OAuth2/PKCE flow) or call `/auth/exchange` to obtain a local JWT.
- User identity is derived from the JWT subject, preventing spoofing attacks.
- For production: ensure `JWT_SECRET` is set to a strong secret, consider shorter token lifetimes, and implement token refresh as needed.
