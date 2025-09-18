Project API README

This project exposes a small FastAPI service to analyze chat-like messages, persist per-message analyses, aggregate daily metrics, and optionally generate human-friendly summaries via an LLM.

Run the server

```bash
cd prompt_analyzer
uvicorn main:app --reload
```

Common notes
- Default host: http://127.0.0.1:8000
- Database: `themes.db` in the project root (auto-created)
- LLM: To enable upleveling, set `GROQ_API_KEY` or `OPENAI_API_KEY` in environment and optionally `GROQ_MODEL`.

Endpoints

1) POST /analyze
- Purpose: Analyze one or more messages, return sentiment, themes, per-message analysis, and optionally persist themes and analyses when `user_id` provided.
- Payload:
  {
    "messages": [{"sender": "user","text": "..."}, ...],
    "user_id": "alice"  # optional
  }
- Sample curl:

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"sender":"user","text":"I feel sad today"},{"sender":"assistant","text":"I am here to help"}],"user_id":"alice"}'
```

2) GET /aggregate/{user_id}/{date}
- Purpose: Aggregate analyses for a given user and date (YYYY-MM-DD). Persists a `daily_summaries` row.
- Sample curl:

```bash
curl http://127.0.0.1:8000/aggregate/alice/2025-09-18
```

3) POST /aggregate_all
- Purpose: Aggregate for all users for a given date (in the JSON body as `date`), defaulting to today (UTC) if omitted.
- Body example: `{ "date": "2025-09-18" }` or `{}` for today.
- Sample curl:

```bash
curl -X POST http://127.0.0.1:8000/aggregate_all -H "Content-Type: application/json" -d '{}'
```

4) POST /uplevel/{user_id}/{date}
- Purpose: Ask the configured LLM to generate a short human-friendly summary for the date's aggregated metrics. Requires LLM API key.
- Body: `{ "include_excerpts": true }` (optional)
- Sample curl:

```bash
curl -X POST http://127.0.0.1:8000/uplevel/alice/2025-09-18 -H "Content-Type: application/json" -d '{"include_excerpts":true}'
```

5) GET /themes/{user_id}
- Purpose: Return stored theme rows for a user.
- Sample curl:

```bash
curl http://127.0.0.1:8000/themes/alice
```

6) GET /themes/{user_id}/since?since=YYYY-MM-DDTHH:MM:SS&summarize=true
- Purpose: Filter theme history since an ISO date and optionally summarize counts.
- Sample curl:

```bash
curl "http://127.0.0.1:8000/themes/alice/since?since=2025-09-01T00:00:00&summarize=true"
```

7) POST /transform
- Purpose: Pseudonymize a prompt before sending to external services.
- Body: `{ "prompt": "My secret is ..." }`
- Sample:

```bash
curl -X POST http://127.0.0.1:8000/transform -H "Content-Type: application/json" -d '{"prompt":"My password is 1234"}'
```

8) POST /log
- Purpose: Simple logging placeholder. Body: `{ "message": "...", "risk_tag": "self_harm" }`

```bash
curl -X POST http://127.0.0.1:8000/log -H "Content-Type: application/json" -d '{"message":"sample","risk_tag":"none"}'
```

9) GET /summary/{user_id}
- Purpose: Placeholder for parent-friendly summary. Sample:

```bash
curl http://127.0.0.1:8000/summary/alice
```

10) GET /status
- Purpose: health check

```bash
curl http://127.0.0.1:8000/status
```

Notes & next steps
- For production, restrict CORS and add authentication to the aggregation endpoints.
- To automate daily aggregation, call `/aggregate_all` from a cron job or scheduler.
- If you want, I can add a small `tasks/daily_aggregate.py` and a sample cron entry.
